# -*- coding: utf-8 -*-
"""Milvus 读写：复用 rag_data_new，通过 data 目录前缀隔离本项目数据。"""

from __future__ import annotations

import threading
from pathlib import Path

from ..core.config import (
    DATA_DIR,
    EMBED_DIM,
    MILVUS_COLLECTION,
    MILVUS_TOKEN,
    MILVUS_URI,
    data_dir_prefix,
)
from .chunker import Chunk
from .embedder import encode_clip_text, encode_image, encode_text, zero_vector

_lock = threading.Lock()
_client = None
OUTPUT_FIELDS = ["text", "db_id", "file_name", "file_path"]


def get_client():
    global _client
    if _client is not None:
        return _client
    with _lock:
        if _client is None:
            from pymilvus import MilvusClient

            _client = MilvusClient(uri=MILVUS_URI, token=MILVUS_TOKEN, timeout=20)
    return _client


def isolation_expr() -> str:
    return f'file_path like "{data_dir_prefix()}%"'


def _expr(extra: str | None = None) -> str:
    base = isolation_expr()
    return f"({base}) and ({extra})" if extra else base


def delete_file(file_id: int) -> None:
    client = get_client()
    expr = _expr(f"db_id == {int(file_id)}")
    client.delete(collection_name=MILVUS_COLLECTION, filter=expr)


def insert_chunks(
    file_id: int,
    file_name: str,
    file_path: str,
    chunks: list[Chunk],
) -> int:
    if not chunks:
        return 0

    client = get_client()
    delete_file(file_id)

    rows = []
    for chunk in chunks:
        image_vector = zero_vector()
        for image in chunk.images:
            if Path(image).is_file():
                try:
                    image_vector = encode_image(image)
                    break
                except Exception:
                    continue

        rows.append(
            {
                "text_vector": encode_text(chunk.text),
                "clip_text_vector": encode_clip_text(chunk.text),
                "clip_image_vector": image_vector,
                "text": chunk.text,
                "db_id": int(file_id),
                "file_name": str(file_name),
                "file_path": str(file_path),
            }
        )

    for start in range(0, len(rows), 16):
        client.insert(collection_name=MILVUS_COLLECTION, data=rows[start : start + 16])

    try:
        client.flush(MILVUS_COLLECTION)
    except Exception:
        pass
    return len(rows)


def _search(vector: list[float], anns_field: str, top_k: int, extra: str | None = None):
    client = get_client()
    results = client.search(
        collection_name=MILVUS_COLLECTION,
        data=[vector],
        anns_field=anns_field,
        limit=top_k,
        filter=_expr(extra),
        output_fields=OUTPUT_FIELDS,
        search_params={"metric_type": "L2", "params": {}},
    )
    hits = []
    for item in results[0]:
        entity = item.get("entity", {}) or {}
        hits.append(
            {
                "text": entity.get("text") or "",
                "file_id": entity.get("db_id"),
                "file_name": entity.get("file_name") or "",
                "file_path": entity.get("file_path") or "",
                "distance": item.get("distance"),
            }
        )
    return hits


def search_text(question: str, top_k: int) -> list[dict]:
    return _search(encode_text(question), "text_vector", top_k)


def search_image(image_path: str | Path, top_k: int) -> list[dict]:
    return _search(encode_image(image_path), "clip_image_vector", top_k)


def count_rows() -> int:
    try:
        client = get_client()
        rows = client.query(
            collection_name=MILVUS_COLLECTION,
            filter="",
            output_fields=["count(*)"],
        )
        return int(rows[0].get("count(*)", 0)) if rows else 0
    except Exception:
        return 0


def collection_exists() -> bool:
    try:
        return MILVUS_COLLECTION in get_client().list_collections()
    except Exception:
        return False


def check_connection() -> tuple[bool, str]:
    try:
        collections = get_client().list_collections()
        return True, f"集合 {MILVUS_COLLECTION} {'存在' if MILVUS_COLLECTION in collections else '不存在'}"
    except Exception as exc:  # pragma: no cover - 依赖外部服务
        return False, f"{type(exc).__name__}: {exc}"
