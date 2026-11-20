from dataclasses import asdict

from elasticsearch import Elasticsearch, NotFoundError
from elasticsearch.helpers import streaming_bulk

from backend.models.index_entry import IndexEntry


VECTOR_PIPELINE = "image-description-text-v1"


class IndexMappingError(ValueError):
    pass


class Index:
    def __init__(self, config: dict):
        options = config["elasticsearch"]
        self.name = options["index"]
        self.dimensions = int(options["dimensions"])
        self.model = config["embedding"]["model"]
        self.client = Elasticsearch(
            options["url"], request_timeout=30,
            **{key: options[key] for key in ("api_key", "basic_auth", "ca_certs") if options.get(key)},
        )

    def check_mapping(self, *, create: bool = False) -> bool:
        if create and not self.client.indices.exists(index=self.name):
            self.client.indices.create(index=self.name, mappings={
                "_meta": {"embedding_model": self.model, "vector_pipeline": VECTOR_PIPELINE},
                "properties": {
                    "entry_id": {"type": "keyword"},
                    "document_id": {"type": "keyword"},
                    "kind": {"type": "keyword"},
                    "page": {"type": "integer"},
                    "related_entry_ids": {"type": "keyword"},
                    "text": {"type": "text"},
                    "image_path": {"type": "keyword"},
                    "vector": {
                        "type": "dense_vector", "dims": self.dimensions,
                        "index": True, "similarity": "cosine",
                    },
                },
            })
        try:
            mapping = self.client.indices.get_mapping(index=self.name)[self.name]["mappings"]
        except NotFoundError:
            if create:
                raise
            return False
        metadata = mapping.get("_meta", {})
        if (metadata.get("embedding_model") != self.model
                or metadata.get("vector_pipeline") != VECTOR_PIPELINE
                or mapping.get("properties", {}).get("vector", {}).get("dims") != self.dimensions):
            raise IndexMappingError(
                f"Elasticsearch 索引「{self.name}」的向量流程、模型或维度不兼容。"
                "请在 config.yaml 中设置一个新的索引名称，重启 API 和 Worker 后重试。"
            )
        return True

    def delete_document(self, document_id: str) -> None:
        try:
            result = self.client.delete_by_query(
                index=self.name, query={"term": {"document_id": document_id}},
                refresh=True,
            )
        except NotFoundError as exc:
            if exc.body.get("error", {}).get("type") != "index_not_found_exception":
                raise
            return
        if result["failures"] or result["timed_out"] or result["version_conflicts"]:
            raise RuntimeError("Elasticsearch did not finish deleting the document.")

    def write(self, rows) -> None:
        actions = (
            {"_index": self.name, "_id": entry.entry_id,
             "_source": {**asdict(entry), "vector": vector}}
            for entry, vector in rows
        )
        for ok, _ in streaming_bulk(self.client, actions, chunk_size=32, raise_on_error=False):
            if not ok:
                raise RuntimeError("Elasticsearch rejected an index entry.")
        result = self.client.indices.refresh(index=self.name)
        if result["_shards"]["failed"]:
            raise RuntimeError("Elasticsearch failed to refresh the index.")

    def search(self, vector: list[float], ready_ids: list[str], *, kind: str) -> list[IndexEntry]:
        response = self.client.search(
            index=self.name, size=10, source_excludes=["vector"],
            knn={
                "field": "vector", "query_vector": vector, "k": 10, "num_candidates": 100,
                "similarity": 0.15,
                "filter": {"bool": {"filter": [
                    {"terms": {"document_id": ready_ids}}, {"term": {"kind": kind}},
                ]}},
            },
        )
        if response["timed_out"] or response["_shards"]["failed"]:
            raise RuntimeError("Elasticsearch search did not complete.")
        return [IndexEntry(**hit["_source"]) for hit in response["hits"]["hits"]]

    def related(self, entry_ids: list[str]) -> dict[str, IndexEntry]:
        if not entry_ids:
            return {}
        response = self.client.mget(index=self.name, ids=entry_ids, source_excludes=["vector"])
        return {hit["_id"]: IndexEntry(**hit["_source"]) for hit in response["docs"] if hit.get("found")}
