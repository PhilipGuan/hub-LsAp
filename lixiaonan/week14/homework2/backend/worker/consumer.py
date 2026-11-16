"""Kafka 消费循环（功能1 / F1-6 + 功能2 状态机 F2-3）。

处理流程：
  消费消息 -> 置 PARSING -> 解析 -> 产物存储 -> 置 SUCCESS -> 手动提交 offset
  失败 -> 重新入队（retry_count+1，指数退避）直至重试耗尽 -> 置 FAILED
"""
import os
import tempfile
import time
from datetime import timedelta

from kafka import KafkaConsumer
from loguru import logger
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import Chunk, ChunkType, Document, DocumentStatus, utcnow
from app.mq.kafka_producer import MQProducer
from app.mq.message_schemas import ParseTaskMessage
from app.services.storage_service import BaseStorage
from app.utils.file_utils import new_id
from worker.indexer.chunker import split_markdown


def _truncate(text: str, limit: int = 800) -> str:
    return text if len(text) <= limit else text[:limit] + "..."


class ParseWorker:
    def __init__(
        self,
        settings: Settings,
        storage: BaseStorage,
        parser,
        session_factory,
        mq: MQProducer,
    ):
        self.settings = settings
        self.storage = storage
        self.parser = parser
        self.session_factory = session_factory
        self.mq = mq

    # ---------- 状态更新（F2-3：以数据库为准，状态可恢复） ----------
    def _mark(self, db: Session, doc: Document, status: DocumentStatus,
              fail_reason: str | None = None, **fields) -> None:
        doc.status = status
        doc.fail_reason = fail_reason
        for key, value in fields.items():
            setattr(doc, key, value)
        db.commit()

    # ---------- 单条消息处理 ----------
    def process_message(self, raw: bytes) -> None:
        try:
            msg = ParseTaskMessage.from_bytes(raw)
        except Exception as exc:
            logger.error(f"非法消息，跳过: {exc} raw={raw[:200]!r}")
            return

        db: Session = self.session_factory()
        try:
            doc = db.get(Document, msg.file_id)
            if doc is None:
                logger.warning(f"任务对应文档不存在，跳过 file_id={msg.file_id}")
                return
            if msg.retry_count > self.settings.kafka_max_retry:
                self._mark(db, doc, DocumentStatus.FAILED,
                           fail_reason="重试次数耗尽", parse_finished_at=utcnow())
                return

            logger.info(
                f"开始解析 task_id={msg.task_id} doc_id={doc.id} "
                f"file={doc.file_name} retry={msg.retry_count} parser={self.parser.name}"
            )
            self._mark(db, doc, DocumentStatus.PARSING,
                       fail_reason=None, parser_name=self.parser.name,
                       parse_started_at=utcnow())

            try:
                fields = self._parse_and_store(db, doc, msg)
                self._mark(db, doc, DocumentStatus.SUCCESS,
                           fail_reason=None, parse_finished_at=utcnow(), **fields)
                logger.info(f"解析完成 doc_id={doc.id} file={doc.file_name}")
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                doc = db.get(Document, msg.file_id)
                if msg.retry_count < self.settings.kafka_max_retry:
                    retry_count = msg.retry_count + 1
                    delay = min(30, 3 * retry_count)
                    logger.warning(
                        f"解析失败，{delay}s 后重新入队 "
                        f"(retry {retry_count}/{self.settings.kafka_max_retry}) "
                        f"doc_id={msg.file_id}: {_truncate(str(exc), 200)}"
                    )
                    time.sleep(delay)
                    self._mark(db, doc, DocumentStatus.PENDING,
                               fail_reason=f"第 {retry_count} 次解析失败，等待重试: {_truncate(str(exc), 300)}")
                    self.mq.send_parse_task(
                        msg.model_copy(update={"retry_count": retry_count})
                    )
                else:
                    logger.error(f"重试耗尽，标记解析失败 doc_id={msg.file_id}: {exc}")
                    self._mark(db, doc, DocumentStatus.FAILED,
                               fail_reason=_truncate(str(exc)), parse_finished_at=utcnow())
        finally:
            db.close()

    # ---------- 解析与产物存储（F1-7 / F1-8） ----------
    def _parse_and_store(self, db: Session, doc: Document, msg: ParseTaskMessage) -> dict:
        pdf_bytes = self.storage.read_bytes(msg.storage_path)
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".pdf")
        try:
            with os.fdopen(tmp_fd, "wb") as fh:
                fh.write(pdf_bytes)
            result = self.parser.parse(tmp_path)
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

        # 1. 图片落盘（F1-8），记录 source_ref -> 可访问 URL 的改写映射
        url_map: dict[str, str] = {}
        image_key_by_url: dict[str, str] = {}
        for idx, image in enumerate(result.images):
            image_key = f"images/{doc.id}/{idx:03d}.{image.ext}"
            self.storage.save(image_key, image.data)
            image_url = self.storage.url_for(image_key)
            url_map[image.source_ref] = image_url
            image_key_by_url[image_url] = image_key

        # 2. Markdown 中的本地图片引用改写为可访问 URL 后存储
        markdown_text = result.markdown
        for source_ref, url in url_map.items():
            markdown_text = markdown_text.replace(f"]({source_ref})", f"]({url})")
        markdown_key = f"markdown/{doc.id}.md"
        self.storage.write_text(markdown_key, markdown_text)

        # 3. 分块（内存中构建，向量化等长操作不持有 DB 写锁，避免 SQLite 多进程锁冲突）
        chunks = split_markdown(
            markdown_text,
            min_chars=self.settings.chunk_min_chars,
            max_chars=self.settings.chunk_max_chars,
        )
        chunk_rows: list[Chunk] = []
        for chunk in chunks:
            chunk_rows.append(Chunk(
                id=new_id(),
                document_id=doc.id,
                seq=chunk.seq,
                chunk_type=ChunkType(chunk.chunk_type),
                content=chunk.content,
                image_url=chunk.image_url,
                page_no=chunk.page_no,
            ))

        # 4. 可选：向量化（F1-8 / F3-3），纯计算不碰数据库
        vectors_data = None
        if self.settings.embedding_enabled:
            vectors_data = self._build_vectors(chunk_rows, doc.id, image_key_by_url)

        # 5. 一次性写库（重解析幂等：先清空旧块），写锁持有窗口极短
        db.query(Chunk).filter(Chunk.document_id == doc.id).delete()
        for row in chunk_rows:
            db.add(row)
        db.flush()
        if vectors_data:
            ids, vectors, metadatas = vectors_data
            from worker.indexer.vector_store import FaissVectorStore

            store = FaissVectorStore(self.settings)
            store.delete_document(doc.id)  # 重解析幂等：先删除该文档旧向量
            store.upsert(ids, vectors, metadatas)
            for row in chunk_rows:
                row.vector_id = row.id  # 向量 ID = 分块 ID，便于回查
            logger.info(f"向量化完成 doc_id={doc.id} vectors={len(ids)}")

        return {"markdown_path": markdown_key, "page_count": result.page_count}

    def _build_vectors(self, rows: list[Chunk], doc_id: str,
                       image_key_by_url: dict[str, str]):
        """构建文本块 + 图片块的向量（图片块 = 可选 LLM 描述 + 周边上下文）。"""
        from worker.indexer.embedder import embed_texts

        texts: list[str] = []
        for idx, row in enumerate(rows):
            if row.chunk_type == ChunkType.TEXT:
                texts.append(row.content)
            else:
                texts.append(self._image_embed_text(rows, idx, image_key_by_url))

        vectors = embed_texts(texts, self.settings)
        metadatas = []
        for row in rows:
            meta = {
                "document_id": doc_id,
                "seq": row.seq,
                "chunk_type": row.chunk_type.value,
                "page_no": row.page_no,
            }
            if row.chunk_type == ChunkType.IMAGE:
                meta["image_key"] = image_key_by_url.get(row.image_url or "", "")
            metadatas.append(meta)
        return [r.id for r in rows], vectors, metadatas

    def _image_embed_text(self, rows: list[Chunk], idx: int,
                          image_key_by_url: dict[str, str]) -> str:
        """图片块的向量文本：可选 LLM 图片描述 + 周边上下文（开放问题2）。"""
        row = rows[idx]
        parts: list[str] = []

        if self.settings.embedding_image_caption and row.image_url:
            image_key = image_key_by_url.get(row.image_url)
            if image_key:
                try:
                    from worker.captioner import generate_caption

                    ext = image_key.rsplit(".", 1)[-1]
                    caption = generate_caption(
                        self.storage.read_bytes(image_key), ext, self.settings
                    )
                    if caption:
                        parts.append(f"图片描述：{caption}")
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"图片描述生成失败（仅用上下文）: {exc}")

        prev_text = next(
            (r.content for r in reversed(rows[:idx]) if r.chunk_type == ChunkType.TEXT), ""
        )
        next_text = next(
            (r.content for r in rows[idx + 1:] if r.chunk_type == ChunkType.TEXT), ""
        )
        context = (prev_text[-200:] + " " + next_text[:200]).strip()
        if context:
            parts.append(f"图片上下文：{context}")
        return " ".join(parts) or "图片"

    # ---------- F2-4：PARSING 超时兜底（可选功能） ----------
    def requeue_stale_parsing(self) -> int:
        """清理停留超过阈值（默认 30 分钟）的 PARSING 任务。

        场景：Worker 进程在解析中途崩溃/掉电，任务永久卡在 PARSING。
        处理：置回 PENDING 并重新入队（自动重试一次）；若再次超时则置 FAILED，
        交由用户通过 F2-5 手动重新解析，避免无限循环。
        返回处理任务数。
        """
        if self.settings.stale_parsing_minutes <= 0:
            return 0
        cutoff = utcnow() - timedelta(minutes=self.settings.stale_parsing_minutes)
        db: Session = self.session_factory()
        try:
            stale = (
                db.query(Document)
                .filter(
                    Document.status == DocumentStatus.PARSING,
                    Document.parse_started_at.isnot(None),
                    Document.parse_started_at < cutoff,
                )
                .all()
            )
            for doc in stale:
                if doc.fail_reason and "超时" in doc.fail_reason:
                    self._mark(
                        db, doc, DocumentStatus.FAILED,
                        fail_reason=f"解析超时（>{self.settings.stale_parsing_minutes} 分钟），"
                                    f"自动重试后仍超时，请检查 PDF 或手动重新解析",
                        parse_finished_at=utcnow(),
                    )
                    logger.warning(f"超时兜底：自动重试后仍超时，置 FAILED doc_id={doc.id}")
                    continue
                self._mark(
                    db, doc, DocumentStatus.PENDING,
                    fail_reason=f"解析超时（>{self.settings.stale_parsing_minutes} 分钟），已自动重新入队",
                )
                self.mq.send_parse_task(ParseTaskMessage(
                    file_id=doc.id, file_name=doc.file_name,
                    storage_path=doc.storage_path, file_hash=doc.file_hash,
                ))
                logger.warning(f"超时兜底：PARSING 超时任务已重新入队 doc_id={doc.id}")
            return len(stale)
        finally:
            db.close()

    # ---------- 消费主循环（F1-6：处理成功才提交 offset） ----------
    def run(self) -> None:
        consumer = KafkaConsumer(
            self.settings.kafka_topic_parse_task,
            bootstrap_servers=self.settings.kafka_bootstrap_servers,
            group_id=self.settings.kafka_consumer_group,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
            consumer_timeout_ms=1000,  # 空闲时定期返回，便于优雅退出
        )
        logger.info(
            f"Worker 开始消费 topic={self.settings.kafka_topic_parse_task} "
            f"group={self.settings.kafka_consumer_group}"
        )
        last_stale_check = 0.0
        try:
            while True:
                now = time.time()
                if now - last_stale_check >= self.settings.stale_check_interval_seconds:
                    last_stale_check = now
                    try:
                        self.requeue_stale_parsing()
                    except Exception as exc:  # noqa: BLE001 兜底自身异常不应中断消费
                        logger.warning(f"超时兜底检查失败（跳过本轮）: {exc}")
                for record in consumer:  # 空闲 1s 后 for 结束，回到外层循环
                    logger.debug(
                        f"收到消息 partition={record.partition} offset={record.offset}"
                    )
                    self.process_message(record.value)
                    consumer.commit()
        except KeyboardInterrupt:
            logger.info("收到退出信号，Worker 停止")
        finally:
            consumer.close()
