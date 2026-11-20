import json
import logging
import os
import re
import shutil
import subprocess
from dataclasses import replace
from html.parser import HTMLParser
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from urllib.parse import unquote
from zipfile import ZipFile

from backend.logging_setup import error_context
from backend.models.document import Status, Task, document_path
from backend.models.index_entry import IndexEntry, asset_path
from backend.models.qa import Image, MAX_IMAGE_BYTES
from backend.service.index import IndexMappingError


logger = logging.getLogger(__name__)
CHUNK_CHARS = 1000


class TableHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text: list[str] = []
        self.images: list[str] = []

    def handle_data(self, data):
        self.text.append(data)

    def handle_starttag(self, tag, attrs):
        source = dict(attrs).get("src")
        if tag == "img" and source:
            self.images.append(source)


def block_content(block: dict) -> tuple[str, list[str]]:
    texts, images = [], []
    for key, value in block.items():
        if key in {"img_path", "image_path", "image_source", "image"} and isinstance(value, str):
            if value:
                images.append(unquote(value) if key == "image_source" else value)
        elif key in {"html", "table_body"} and isinstance(value, str):
            parser = TableHTML()
            parser.feed(value)
            texts.extend(parser.text)
            images.extend(parser.images)
        elif isinstance(value, dict):
            text, paths = block_content(value)
            texts.append(text)
            images.extend(paths)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    text, paths = block_content(item)
                    texts.append(text)
                    images.extend(paths)
                elif isinstance(item, str) and ("caption" in key or "footnote" in key or key == "list_items"):
                    texts.append(item)
        elif key in {"text", "content", "latex"} and isinstance(value, str):
            if "<table" in value.lower() or "<img" in value.lower():
                parser = TableHTML()
                parser.feed(value)
                texts.extend(parser.text)
                images.extend(parser.images)
            else:
                texts.append(value)
    return " ".join(part.strip() for part in texts if part.strip()), images


def structured_blocks(parsed_dir: Path):
    files = sorted(parsed_dir.rglob("structured_content.json"))
    if not files:
        files = sorted(parsed_dir.rglob("*_content_list.json"))
    if len(files) != 1:
        raise ValueError("MinerU 未生成唯一的结构化解析结果。")
    source = files[0]
    payload = json.loads(source.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        for block in payload:
            yield source.parent, int(block.get("page_idx", 0)), block
    else:
        for page in payload["pages"]:
            for block in page["blocks"]:
                yield source.parent, int(page["page_idx"]), block


def entries(data_dir: Path, parsed_dir: Path, document_id: str) -> list[IndexEntry]:
    data_dir = data_dir.resolve()
    rows: list[IndexEntry] = []
    figures: dict[Path, IndexEntry] = {}
    nearby: list[tuple[IndexEntry, IndexEntry | None]] = []
    last_text = None
    for base, page, block in structured_blocks(parsed_dir):
        text, images = block_content(block)
        own_text = None
        for offset in range(0, len(text), CHUNK_CHARS):
            entry = IndexEntry(f"{document_id}-{len(rows)}", document_id, "text", page,
                               text=text[offset:offset + CHUNK_CHARS])
            rows.append(entry)
            own_text = own_text or entry
            last_text = entry
        for reference in images:
            if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:|^//", reference):
                continue  # Only index assets emitted by the local parser.
            path = (base / reference).resolve()
            if not path.is_relative_to(parsed_dir.resolve()) or not path.is_file():
                raise ValueError("MinerU 结构化结果引用了缺失的本地图片。")
            figure = figures.get(path)
            if figure is None:
                figure = IndexEntry(f"{document_id}-{len(rows)}", document_id, "image", page,
                                    image_path=path.relative_to(data_dir).as_posix())
                rows.append(figure)
                figures[path] = figure
            candidate = own_text or (last_text if last_text and last_text.page == page else None)
            nearby.append((figure, candidate))
    first_text = {}
    for entry in rows:
        if entry.kind == "text":
            first_text.setdefault(entry.page, entry)
    for figure, candidate in nearby:
        candidate = candidate or first_text.get(figure.page)
        if candidate and candidate.entry_id not in figure.related_entry_ids:
            figure.related_entry_ids.append(candidate.entry_id)
            candidate.related_entry_ids.append(figure.entry_id)
    if not rows:
        raise ValueError("MinerU 未提取到正文或原图。")
    return rows


def parse_document(settings: dict, source: Path, output: Path) -> None:
    output.mkdir()
    command = [arg.replace("{input}", str(source)).replace("{output}", str(output))
               for arg in settings["command"]]
    # 继承 Worker 的输出，让 CLI 进度和错误一起进入终端或 tee 日志。
    subprocess.run(command, check=True, timeout=settings["timeout_seconds"],
                   stderr=subprocess.STDOUT, env={**os.environ, "PYTHONUNBUFFERED": "1"})
    archives = sorted(output.glob("*.zip"))
    if len(archives) > 1:
        raise ValueError("MinerU 生成了多个 ZIP 文件。")
    if archives:
        with ZipFile(archives[0]) as archive:
            if any(not (output / name).resolve().is_relative_to(output.resolve())
                   for name in archive.namelist()):
                raise ValueError("MinerU ZIP 中包含不安全的路径。")
            archive.extractall(output)


FAILURE_MESSAGES = {
    "prepare": "清理旧索引或读取原始文档 失败，请检查磁盘和 Elasticsearch。",
    "mineru": "MinerU 执行失败，请检查命令、模型及超时设置。",
    "extract": "MinerU 结构化结果或原图读取失败，请检查解析产物。",
    "describe": "生成图片说明失败，请检查 VLM 和 API 配置。",
    "embedding": "文本向量生成失败，请检查模型和 API 配置。",
    "index": "索引映射检查或写入失败，请检查 Elasticsearch 配置。",
}


def process(task: Task, config: dict, documents, index, embedding, vision) -> None:
    with documents.lock(task.document_id, wait=True):
        document = documents.get(task.document_id)
        # 同一尝试可在 Worker 重启后恢复。已经完成或过期的消息直接跳过。
        if (document is None or Task.for_document(document) != task
                or document.status not in (Status.QUEUED, Status.PROCESSING)):
            return
        document = replace(document, status=Status.PROCESSING)
        documents.save(document)
        logger.info("ingest_started document_id=%s attempt_id=%s", task.document_id, task.attempt_id)
        started = monotonic()
        output = documents.data_dir / document_path(task.document_id) / "parsed"
        stage = "prepare"
        try:
            source = documents.data_dir / task.file_path
            if not source.is_file():
                raise FileNotFoundError
            stage = "index"
            index.check_mapping(create=True)
            index.delete_document(task.document_id)
            stage = "mineru"
            if output.exists():
                shutil.rmtree(output)
            for leftover in output.parent.glob(".mineru-*"):
                shutil.rmtree(leftover)
            # 先在独立目录解析，命令成功后再替换正式解析目录。
            with TemporaryDirectory(prefix=".mineru-", dir=output.parent) as temp:
                staging = Path(temp) / "parsed"
                logger.info("mineru_started document_id=%s attempt_id=%s extension=%s",
                            task.document_id, task.attempt_id, source.suffix)
                parse_document(config["mineru"], source, staging)
                staging.rename(output)
            logger.info("mineru_finished document_id=%s attempt_id=%s seconds=%.2f",
                        task.document_id, task.attempt_id, monotonic() - started)
            stage = "extract"
            rows = entries(documents.data_dir, output, task.document_id)
            image_count = sum(bool(row.image_path) for row in rows)
            stage = "index"
            indexing = monotonic()

            def encoded_rows():
                nonlocal stage
                image_number = 0
                # ES 逐批消费向量生成器。分别标注编码失败和索引失败。
                for entry in rows:
                    if entry.image_path:
                        stage = "extract"
                        image = asset_path(documents.data_dir, output, entry.image_path)
                        with image.open("rb") as file:
                            picture = Image.from_bytes(file.read(MAX_IMAGE_BYTES + 1))
                        stage = "describe"
                        image_number += 1
                        describe_started = monotonic()
                        logger.info("image_describe_started document_id=%s attempt_id=%s image_id=%s progress=%d/%d",
                                    task.document_id, task.attempt_id, entry.entry_id, image_number, image_count)
                        description = vision.describe(picture)
                        logger.info("image_describe_finished document_id=%s attempt_id=%s image_id=%s progress=%d/%d seconds=%.2f",
                                    task.document_id, task.attempt_id, entry.entry_id, image_number, image_count,
                                    monotonic() - describe_started)
                        entry = replace(entry, text=description)
                    stage = "embedding"
                    vector = embedding.encode(entry.text)
                    stage = "index"
                    yield entry, vector

            index.write(encoded_rows())
        except Exception as exc:
            message = str(exc) if isinstance(exc, IndexMappingError) else FAILURE_MESSAGES[stage]
            documents.save(replace(document, status=Status.FAILED,
                                   failure_stage=stage, error=message))
            logger.error(
                "ingest_failed document_id=%s attempt_id=%s stage=%s error=%s seconds=%.2f",
                task.document_id, task.attempt_id, stage, error_context(exc), monotonic() - started,
            )
            return
        # Index.write 已刷新索引。此时才允许问答检索该文档。
        documents.save(replace(document, status=Status.READY))
    logger.info("index_ready document_id=%s attempt_id=%s entries=%d image_entries=%d index_seconds=%.2f total_seconds=%.2f",
                task.document_id, task.attempt_id, len(rows), image_count,
                monotonic() - indexing, monotonic() - started)
