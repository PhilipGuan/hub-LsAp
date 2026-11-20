import json
import logging
import shutil
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import datetime, timezone
from fcntl import LOCK_EX, LOCK_NB, flock
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4
from zipfile import BadZipFile, ZipFile

from confluent_kafka import Producer
from redis import Redis

from backend.logging_setup import error_context
from backend.models.document import DOCUMENT_EXTENSIONS, MAX_DOCUMENT_BYTES, Document, Status, Task, document_path


logger = logging.getLogger(__name__)


class DocumentError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


class Documents:
    def __init__(self, config: dict, index):
        self.data_dir: Path = config["data_dir"]
        self.records = f'{config["redis"]["prefix"]}:documents'
        self.redis = Redis.from_url(config["redis"]["url"], decode_responses=True,
                                    socket_timeout=5, socket_connect_timeout=5)
        self.kafka = config["kafka"]
        self.index = index

    def save(self, document: Document) -> None:
        self.redis.hset(self.records, document.document_id, json.dumps(asdict(document)))

    @staticmethod
    def _decode(raw: str) -> Document:
        record = json.loads(raw)
        record["status"] = Status(record["status"])
        return Document(**record)

    def get(self, document_id: str) -> Document | None:
        raw = self.redis.hget(self.records, document_id)
        return self._decode(raw) if raw else None

    def list(self) -> list[Document]:
        documents = [self._decode(raw) for raw in self.redis.hvals(self.records)]
        return sorted(documents, key=lambda doc: doc.created_at, reverse=True)

    @contextmanager
    def lock(self, document_id: str, *, wait: bool = False):
        # Keep lock filenames stable across deletion and bound their total count.
        bucket = document_path(document_id).name[:2]
        lock_dir = self.data_dir / ".locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        with (lock_dir / bucket).open("a") as handle:
            try:
                flock(handle, LOCK_EX if wait else LOCK_EX | LOCK_NB)
            except BlockingIOError:
                raise DocumentError(409, "文档操作进行中，请稍后再试。") from None
            yield

    def _publish(self, task: Task) -> None:
        producer = Producer({
            **self.kafka["options"], "enable.idempotence": True, "message.timeout.ms": 10000,
        })
        result = []
        producer.produce(
            self.kafka["topic"],
            key=task.document_id,
            value=json.dumps(asdict(task)).encode(),
            on_delivery=lambda error, _: result.append(error),
        )
        if producer.flush(11) or result != [None]:
            raise RuntimeError("Kafka did not acknowledge the task.")

    def _queue(self, document: Document) -> Document:
        # 先持久化待解析记录。Worker 启动时可补发未确认的任务。
        self.save(document)
        task = Task.for_document(document)
        try:
            self._publish(task)
        except Exception as exc:
            self.save(replace(document, status=Status.FAILED, failure_stage="dispatch",
                              error="Kafka 任务投递失败，请重试。"))
            logger.warning("dispatch_failed document_id=%s attempt_id=%s error=%s",
                           task.document_id, task.attempt_id, error_context(exc))
            raise DocumentError(503, "Kafka 任务投递失败，请在文档列表重试。") from None
        document = replace(document, published=True)
        self.save(document)
        logger.info("task_queued document_id=%s attempt_id=%s retries=%d",
                    task.document_id, task.attempt_id, document.retries)
        return document

    def upload(self, filename: str, file: BinaryIO) -> Document:
        suffix = Path(filename).suffix.lower()
        if suffix not in DOCUMENT_EXTENSIONS:
            raise DocumentError(400, "请选择 PDF 或 Word（.doc、.docx）文件。")
        first = file.read(1024)
        if suffix == ".pdf" and b"%PDF-" not in first:
            raise DocumentError(400, "文件内容与 PDF 格式不符。")
        if suffix == ".doc" and not first.startswith(bytes.fromhex("D0 CF 11 E0 A1 B1 1A E1")):
            raise DocumentError(400, "文件内容与 Word .doc 格式不符。")
        document = Document(uuid4().hex, filename, uuid4().hex, Status.QUEUED,
                            datetime.now(timezone.utc).isoformat())
        source = self.data_dir / Task.for_document(document).file_path
        folder = source.parent
        with self.lock(document.document_id, wait=True):
            folder.mkdir(parents=True, exist_ok=False)
            try:
                with source.open("xb") as target:
                    target.write(first)
                    size = len(first)
                    while chunk := file.read(1024 * 1024):
                        size += len(chunk)
                        if size > MAX_DOCUMENT_BYTES:
                            raise DocumentError(413, "文件大小不能超过 50 MiB。")
                        target.write(chunk)
                if suffix == ".docx":
                    try:
                        with ZipFile(source) as archive:
                            archive.getinfo("[Content_Types].xml")
                            archive.getinfo("word/document.xml")
                    except (BadZipFile, KeyError):
                        raise DocumentError(400, "文件内容与 Word .docx 格式不符。") from None
            except Exception:
                shutil.rmtree(folder)
                raise
            return self._queue(document)

    def retry(self, document_id: str) -> Document:
        with self.lock(document_id):
            document = self.get(document_id)
            if document is None:
                raise DocumentError(404, "文档不存在。")
            if document.status != Status.FAILED:
                raise DocumentError(409, "只能重试解析失败的文档。")
            if not (self.data_dir / Task.for_document(document).file_path).is_file():
                raise DocumentError(409, "原始文件已丢失，请重新上传。")
            return self._queue(replace(
                document, attempt_id=uuid4().hex, status=Status.QUEUED,
                retries=document.retries + 1, failure_stage=None, error=None, published=False,
                retry_history=[*document.retry_history, {
                    "attempt_id": document.attempt_id,
                    "failure_stage": document.failure_stage,
                    "error": document.error,
                    "retried_at": datetime.now(timezone.utc).isoformat(),
                }],
            ))

    def recover_queued(self) -> None:
        # Recover the gap between persisting a queued record and publishing it.
        for pending in self.list():
            if pending.status != Status.QUEUED or pending.published:
                continue
            with self.lock(pending.document_id, wait=True):
                current = self.get(pending.document_id)
                if current == pending:
                    try:
                        self._queue(current)
                    except DocumentError:
                        pass

    def delete(self, document_id: str) -> None:
        with self.lock(document_id):
            document = self.get(document_id)
            if document is None:
                raise DocumentError(404, "文档不存在。")
            if document.status not in (Status.READY, Status.FAILED, Status.DELETING):
                raise DocumentError(409, "当前状态不能删除文档。")
            deleting = replace(document, status=Status.DELETING, failure_stage=None, error=None)
            # 清理前先撤下检索资格，即使 ES 删除失败也不会再被召回。
            self.save(deleting)
            try:
                self.index.delete_document(document_id)
                folder = self.data_dir / document_path(document_id)
                if folder.exists():
                    shutil.rmtree(folder)
            except Exception as exc:
                self.save(replace(deleting, failure_stage="delete", error="清理失败，请重试删除。"))
                logger.warning("delete_failed document_id=%s attempt_id=%s error=%s",
                               document_id, document.attempt_id, error_context(exc))
                raise DocumentError(503, "清理失败，请重试删除。") from None
            self.redis.hdel(self.records, document_id)
            logger.info("document_deleted document_id=%s attempt_id=%s",
                        document_id, document.attempt_id)
