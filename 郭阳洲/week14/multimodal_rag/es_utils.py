from elasticsearch import Elasticsearch
import config

_es = None


def get_es():
    global _es
    if _es is None:
        _es = Elasticsearch(config.ES_URL)
    return _es


def ensure_index(dim):
    """确保索引存在；尚未创建时按 embedding 维度建 dense_vector 映射"""
    es = get_es()
    if es.indices.exists(index=config.ES_INDEX):
        return
    es.indices.create(
        index=config.ES_INDEX,
        mappings={
            "properties": {
                "doc_id": {"type": "keyword"},
                "filename": {"type": "keyword"},
                "text": {"type": "text"},
                "images": {"type": "keyword"},
                "embedding": {
                    "type": "dense_vector",
                    "dims": dim,
                    "index": True,
                    "similarity": "cosine",
                },
            }
        },
    )


def index_chunk(doc_id, filename, text, embedding, images):
    """写入一个文本切块及其向量、关联图片"""
    get_es().index(
        index=config.ES_INDEX,
        document={
            "doc_id": doc_id,
            "filename": filename,
            "text": text,
            "images": images,
            "embedding": embedding,
        },
    )


def search(query_vector, top_k=4):
    """向量检索，返回最相关的若干切块（含文本与关联图片）"""
    resp = get_es().search(
        index=config.ES_INDEX,
        knn={
            "field": "embedding",
            "query_vector": query_vector,
            "k": top_k,
            "num_candidates": min(top_k * 5, 50),
        },
        size=top_k,
        source=["text", "images", "filename"],
    )
    hits = []
    for h in resp["hits"]["hits"]:
        src = h["_source"]
        hits.append({
            "score": h.get("_score"),
            "text": src.get("text", ""),
            "images": src.get("images", []),
            "filename": src.get("filename", ""),
        })
    return hits