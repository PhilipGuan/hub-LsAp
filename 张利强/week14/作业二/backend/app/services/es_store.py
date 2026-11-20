"""Elasticsearch 存储与 BM25 + kNN 混合检索（RRF 融合）。"""

import logging

from elasticsearch import Elasticsearch, NotFoundError

from ..config import get_settings

log = logging.getLogger(__name__)
settings = get_settings()


def _client() -> Elasticsearch:
    return Elasticsearch(settings.es_url, request_timeout=60)


def ensure_index() -> None:
    client = _client()
    if client.indices.exists(index=settings.es_index):
        return
    client.indices.create(
        index=settings.es_index,
        settings={"number_of_replicas": 0},
        mappings={
            "properties": {
                "doc_id": {"type": "keyword"},
                "chunk_id": {"type": "keyword"},
                "filename": {"type": "keyword"},
                "heading": {"type": "text", "analyzer": "cjk"},
                "page": {"type": "integer"},
                "content": {"type": "text", "analyzer": "cjk"},
                "images": {"type": "keyword"},
                "content_vector": {
                    "type": "dense_vector",
                    "dims": settings.embed_dim,
                    "index": True,
                    "similarity": "cosine",
                },
            }
        },
    )
    log.info("created index %s", settings.es_index)


def index_chunks(operations: list[dict]) -> int:
    if not operations:
        return 0
    ensure_index()
    client = _client()
    resp = client.bulk(operations=operations, refresh=True)
    if resp.get("errors"):
        failed = [i for i in resp.get("items", []) if i.get("index", {}).get("error")]
        raise RuntimeError(f"bulk indexing failed: {failed[:2]}")
    return len(operations) // 2


def delete_document(doc_id: str) -> int:
    client = _client()
    try:
        resp = client.delete_by_query(
            index=settings.es_index,
            query={"term": {"doc_id": doc_id}},
            refresh=True,
            conflicts="proceed",
        )
        return resp.get("deleted", 0)
    except NotFoundError:
        return 0


def _hit(h: dict) -> dict:
    src = h.get("_source", {})
    return {
        "chunk_id": src.get("chunk_id"),
        "doc_id": src.get("doc_id"),
        "filename": src.get("filename"),
        "heading": src.get("heading", ""),
        "page": src.get("page", 0),
        "content": src.get("content", ""),
        "images": src.get("images", []),
        "score": h.get("_score"),
    }


def search(query_text: str, query_vector: list[float], top_k: int = 6, pool: int = 20) -> list[dict]:
    client = _client()
    ensure_index()

    bm25_hits: list[dict] = []
    if query_text.strip():
        try:
            resp = client.search(
                index=settings.es_index,
                query={"match": {"content": query_text[:500]}},
                size=pool,
                source=["chunk_id", "doc_id", "filename", "heading", "page", "content", "images"],
            )
            bm25_hits = [_hit(h) for h in resp["hits"]["hits"]]
        except Exception:
            log.exception("bm25 search failed")

    knn_hits: list[dict] = []
    try:
        resp = client.search(
            index=settings.es_index,
            knn={
                "field": "content_vector",
                "query_vector": query_vector,
                "k": pool,
                "num_candidates": max(pool * 5, 100),
            },
            size=pool,
            source=["chunk_id", "doc_id", "filename", "heading", "page", "content", "images"],
        )
        knn_hits = [_hit(h) for h in resp["hits"]["hits"]]
    except Exception:
        log.exception("knn search failed")

    fused: dict[str, dict] = {}
    k = 60
    for rank, hit in enumerate(bm25_hits):
        cid = hit["chunk_id"]
        if cid not in fused:
            fused[cid] = {**hit, "score": 0.0}
        fused[cid]["score"] += 1.0 / (k + rank + 1)
    for rank, hit in enumerate(knn_hits):
        cid = hit["chunk_id"]
        if cid not in fused:
            fused[cid] = {**hit, "score": 0.0}
        fused[cid]["score"] += 1.0 / (k + rank + 1)

    ranked = sorted(fused.values(), key=lambda x: x["score"], reverse=True)
    return ranked[:top_k]


def health() -> dict:
    try:
        client = _client()
        return {"ok": True, "cluster": client.info().get("cluster_name")}
    except Exception as exc:  # pragma: no cover
        return {"ok": False, "error": str(exc)}
