"""
Milvus 向量库封装。

一个 chunk 存三路向量：
    text_vector       -> bge 文本向量，文本问答检索走这里
    clip_text_vector  -> CLIP 文本向量，用文字检索图片
    clip_image_vector -> CLIP 图片向量，用图片检索图片
"""
import logging
from pathlib import Path
from typing import Dict, List, Optional

from config import (
    BGE_DIM,
    CLIP_DIM,
    MILVUS_COLLECTION,
    MILVUS_TOKEN,
    MILVUS_URI,
)
from embeddings import encode_chunk

logger = logging.getLogger(__name__)

_client = None

OUTPUT_FIELDS = [
    "doc_id",
    "chunk_index",
    "file_name",
    "md_path",
    "image_dir",
    "has_image",
    "text",
]


def _connect():
    from pymilvus import MilvusClient

    kwargs = {}
    if MILVUS_TOKEN:
        kwargs["token"] = MILVUS_TOKEN
        kwargs["uri"] = MILVUS_URI
    else:
        kwargs["uri"] = MILVUS_URI
    return MilvusClient(**kwargs)


def _ensure_collection(client) -> None:
    from pymilvus import DataType

    if client.has_collection(MILVUS_COLLECTION):
        return

    schema = client.create_schema(auto_id=True, enable_dynamic_field=False)
    schema.add_field("id", DataType.INT64, is_primary=True)
    schema.add_field("doc_id", DataType.INT64)
    schema.add_field("chunk_index", DataType.INT64)
    schema.add_field("file_name", DataType.VARCHAR, max_length=512)
    schema.add_field("md_path", DataType.VARCHAR, max_length=2048)
    schema.add_field("image_dir", DataType.VARCHAR, max_length=2048)
    schema.add_field("has_image", DataType.BOOL)
    schema.add_field("text", DataType.VARCHAR, max_length=65535)
    schema.add_field("text_vector", DataType.FLOAT_VECTOR, dim=BGE_DIM)
    schema.add_field("clip_text_vector", DataType.FLOAT_VECTOR, dim=CLIP_DIM)
    schema.add_field("clip_image_vector", DataType.FLOAT_VECTOR, dim=CLIP_DIM)

    index_params = client.prepare_index_params()
    for field in ("text_vector", "clip_text_vector", "clip_image_vector"):
        index_params.add_index(
            field_name=field, index_type="AUTOINDEX", metric_type="IP"
        )

    try:
        client.create_collection(
            collection_name=MILVUS_COLLECTION,
            schema=schema,
            index_params=index_params,
        )
        logger.info("已创建 Milvus collection: %s", MILVUS_COLLECTION)
    except Exception:
        # 多进程同时首次启动时可能发生竞争：另一个进程已经建好了
        if not client.has_collection(MILVUS_COLLECTION):
            raise


def get_client():
    global _client
    if _client is None:
        _client = _connect()
        _ensure_collection(_client)
    return _client


def insert_chunks(
    doc_id: int,
    file_name: str,
    md_path: Path,
    chunks: List[str],
) -> int:
    """编码并写入一个文档的所有 chunk，返回写入条数。"""
    if not chunks:
        return 0

    md_path = Path(md_path)
    image_dir = str(md_path.parent)

    rows = []
    for index, chunk in enumerate(chunks):
        encoded = encode_chunk(chunk, image_dir)
        rows.append(
            {
                "doc_id": int(doc_id),
                "chunk_index": index,
                "file_name": file_name,
                "md_path": str(md_path),
                "image_dir": image_dir,
                "has_image": encoded.has_image,
                "text": chunk,
                "text_vector": encoded.text_vector,
                "clip_text_vector": encoded.clip_text_vector,
                "clip_image_vector": encoded.clip_image_vector,
            }
        )

    client = get_client()
    # 分批写入，避免单次请求过大（Milvus 对单次 insert 有大小限制）
    batch_size = 64
    for start in range(0, len(rows), batch_size):
        client.insert(
            collection_name=MILVUS_COLLECTION,
            data=rows[start : start + batch_size],
        )

    logger.info("文档 %s 写入 %d 个 chunk", doc_id, len(rows))
    return len(rows)


def search(
    anns_field: str,
    vector: List[float],
    limit: int,
    output_fields: Optional[List[str]] = None,
) -> List[Dict]:
    """向量检索，异常时返回空列表（前端不至于直接崩掉）。"""
    if not vector:
        return []
    try:
        results = get_client().search(
            collection_name=MILVUS_COLLECTION,
            data=[vector],
            anns_field=anns_field,
            limit=int(limit),
            output_fields=output_fields or OUTPUT_FIELDS,
        )
        return results[0] if results else []
    except Exception as exc:
        logger.warning("Milvus 检索失败(%s): %s", anns_field, exc)
        return []


def delete_document(doc_id: int) -> None:
    """删除一个文档在向量库中的所有 chunk。"""
    try:
        client = get_client()
        if not client.has_collection(MILVUS_COLLECTION):
            return
        client.delete(
            collection_name=MILVUS_COLLECTION, filter=f"doc_id == {int(doc_id)}"
        )
        logger.info("已删除文档 %s 的向量", doc_id)
    except Exception as exc:
        logger.warning("删除文档 %s 向量失败: %s", doc_id, exc)
