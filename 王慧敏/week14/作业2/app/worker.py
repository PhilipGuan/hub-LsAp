# -*- coding: utf-8 -*-
"""PDF 解析 Worker：独立进程运行，消费 Kafka 解析任务。

运行方式:
    python -m app.worker

数据流:
    FastAPI 收到上传 -> 写 SQLite(待解析) -> 发 Kafka 任务
        -> [本文件] 消费任务 -> 状态改"解析中" -> MinerU 解析
        -> 切分 chunk 写入 Chroma -> 状态改"解析完成"/"解析失败"
"""

import os

from app import db, vector_store
from app.config import PARSED_DIR, STATUS_DONE, STATUS_FAILED, STATUS_PARSING
from app.kafka_bus import consume_loop, get_consumer
from app.mineru_parser import MinerUParseError, parse_pdf


def handle_parse_task(key, data):
    doc_id = data["doc_id"]
    file_path = data["file_path"]
    print(f"[worker] 开始解析文档 doc_id={doc_id} file={file_path}")

    db.update_status(doc_id, STATUS_PARSING)
    try:
        markdown_text, _images = parse_pdf(file_path, doc_id)
        md_dir = os.path.join(PARSED_DIR, doc_id)
        # parse_pdf 内部已定位真实 markdown 所在目录，这里重新找一次用于切分时解析相对图片路径
        md_dir = _locate_markdown_dir(md_dir) or md_dir

        chunks = vector_store.split_markdown_with_images(markdown_text, md_dir)
        chunk_count = vector_store.add_document_chunks(doc_id, chunks)
        image_count = vector_store.count_images(chunks)

        db.update_status(doc_id, STATUS_DONE, chunk_count=chunk_count, image_count=image_count)
        print(f"[worker] 解析完成 doc_id={doc_id} chunks={chunk_count} images={image_count}")
    except MinerUParseError as exc:
        db.update_status(doc_id, STATUS_FAILED, error_msg=str(exc))
        print(f"[worker] 解析失败 doc_id={doc_id}: {exc}")
    except Exception as exc:  # noqa: BLE001
        db.update_status(doc_id, STATUS_FAILED, error_msg=str(exc))
        print(f"[worker] 解析异常 doc_id={doc_id}: {exc}")
        raise  # 让 kafka_bus 不提交 offset，支持重试


def _locate_markdown_dir(out_dir):
    for root, _dirs, files in os.walk(out_dir):
        for name in files:
            if name.endswith(".md"):
                return root
    return None


if __name__ == "__main__":
    db.init_db()
    print("[worker] 启动，等待解析任务...")
    consumer = get_consumer()
    consume_loop(consumer, handle_parse_task)
