"""Milvus Lite 存储：建集合、插入分块、文本/图像检索。"""
import logging
from typing import Dict, List, Optional

from pymilvus import DataType, MilvusClient

from . import config
from .embedder import IMAGE_DIM, TEXT_DIM, embed_image_paths, embed_text

log = logging.getLogger(__name__)

_client: Optional[MilvusClient] = None


def get_client() -> MilvusClient:
    global _client
    if _client is None:
        _client = MilvusClient(config.MILVUS_DB_FILE)
    return _client


def create_collection_if_missing() -> None:
    """按 demo 的 schema 建集合，多一个 doc_id 字段用于区分文档。"""
    client = get_client()
    if client.has_collection(config.COLLECTION_NAME):
        return

    schema = client.create_schema(auto_id=True, enable_dynamic_field=False)
    schema.add_field(field_name="id", datatype=DataType.INT64, is_primary=True)
    schema.add_field(field_name="doc_id", datatype=DataType.VARCHAR, max_length=64)
    schema.add_field(field_name="text", datatype=DataType.VARCHAR, max_length=65535)
    schema.add_field(field_name="image_path", datatype=DataType.VARCHAR, max_length=1024)
    schema.add_field(field_name="text_embedding", datatype=DataType.FLOAT_VECTOR, dim=TEXT_DIM)
    schema.add_field(field_name="image_embedding", datatype=DataType.FLOAT_VECTOR, dim=IMAGE_DIM)
    schema.add_field(field_name="has_image", datatype=DataType.BOOL)

    index_params = client.prepare_index_params()
    index_params.add_index(
        field_name="text_embedding",
        index_type="IVF_FLAT",
        metric_type="COSINE",
        params={"nlist": 128},
    )
    index_params.add_index(
        field_name="image_embedding",
        index_type="IVF_FLAT",
        metric_type="COSINE",
        params={"nlist": 128},
    )

    client.create_collection(
        collection_name=config.COLLECTION_NAME,
        schema=schema,
        index_params=index_params,
    )
    log.info("已创建集合 %s", config.COLLECTION_NAME)


def insert_chunks(doc_id: str, chunks: List[Dict]) -> int:
    """向量化并插入分块。

    chunks 每项: {"text": str, "images": [绝对图片路径], "image_rel": [相对图片路径]}
    """
    data = []
    for chunk in chunks:
        text = chunk["text"]
        imgs = chunk.get("images") or []
        rels = chunk.get("image_rel") or []

        t_emb = embed_text([text])[0].tolist()

        if imgs:
            try:
                i_emb = embed_image_paths([imgs[0]])[0].tolist()
                img_path = rels[0] if rels else imgs[0]
                has_img = True
            except Exception as e:
                log.warning("图像嵌入失败，跳过该图片: %s (%s)", imgs[0], e)
                i_emb = [0.0] * IMAGE_DIM
                img_path = ""
                has_img = False
        else:
            i_emb = [0.0] * IMAGE_DIM
            img_path = ""
            has_img = False

        data.append({
            "doc_id": doc_id,
            "text": text,
            "image_path": img_path,
            "text_embedding": t_emb,
            "image_embedding": i_emb,
            "has_image": has_img,
        })

    if data:
        get_client().insert(collection_name=config.COLLECTION_NAME, data=data)
    return len(data)


def _load() -> None:
    """Milvus Lite 每次进程重启后集合回到 released 状态，检索前必须 load。"""
    get_client().load_collection(collection_name=config.COLLECTION_NAME)


def search(query_vector: List[float], field: str, top_k: int) -> List[Dict]:
    """通用向量检索，返回命中列表。

    每项: {"doc_id", "text", "image_path", "has_image", "score"}
    """
    try:
        _load()
        results = get_client().search(
            collection_name=config.COLLECTION_NAME,
            data=[query_vector],
            anns_field=field,
            limit=top_k,
            output_fields=["doc_id", "text", "image_path", "has_image"],
            search_params={"metric_type": "COSINE", "params": {"nprobe": 16}},
        )
    except Exception as e:
        # 集合为空 / 未入库 / 索引未就绪等情况，按无结果处理
        log.warning("检索失败: %s", e)
        return []

    hits = []
    for hit in results[0]:
        hits.append({
            "doc_id": hit["entity"].get("doc_id", ""),
            "text": hit["entity"].get("text", ""),
            "image_path": hit["entity"].get("image_path", ""),
            "has_image": hit["entity"].get("has_image", False),
            "score": hit.get("distance", 0.0),
        })
    return hits


def search_text(query_vector: List[float], top_k: int) -> List[Dict]:
    return search(query_vector, "text_embedding", top_k)


def search_image(query_vector: List[float], top_k: int) -> List[Dict]:
    return search(query_vector, "image_embedding", top_k)
