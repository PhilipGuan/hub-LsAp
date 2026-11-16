"""Kafka 消息处理：解析 → 分块 → 向量化 → 入库 → 更新状态。"""
import logging
from pathlib import Path

from . import chunking, config, docs_db, milvus_store, mineru_parser

log = logging.getLogger(__name__)


def handle_parse_message(msg: dict) -> None:
    """处理一条解析请求消息。失败时把状态置为 failed 并记录原因。"""
    doc_id = msg.get("doc_id", "")
    pdf_path = Path(msg.get("pdf_path", ""))
    if not doc_id or not pdf_path.exists():
        log.warning("无效消息或 PDF 不存在: %s", msg)
        return

    doc = docs_db.get_doc(doc_id)
    if doc and doc["status"] == docs_db.STATUS_DONE:
        log.info("文档 %s 已完成解析，跳过重复消息", doc_id)
        return

    try:
        docs_db.update_status(doc_id, docs_db.STATUS_PARSING)

        out_dir = config.PARSED_DIR / doc_id
        result = mineru_parser.parse_pdf(pdf_path, out_dir)

        chunks = chunking.load_and_chunk(result["markdown"], result["images_dir"])
        # 图片路径存为相对路径 {doc_id}/images/{name}，便于前端访问
        for chunk in chunks:
            chunk["image_rel"] = [
                f"{doc_id}/images/{Path(p).name}" for p in chunk["images"]
            ]

        count = milvus_store.insert_chunks(doc_id, chunks)
        docs_db.update_status(doc_id, docs_db.STATUS_DONE)
        log.info("文档 %s 解析完成，入库 %d 个分块", doc_id, count)
    except Exception as e:
        log.exception("文档 %s 解析失败", doc_id)
        docs_db.update_status(doc_id, docs_db.STATUS_FAILED, str(e))
