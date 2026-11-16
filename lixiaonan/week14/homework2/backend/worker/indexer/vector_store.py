"""向量库适配层（F1-8 / F3-3）。

M4 提供 FAISS IndexIDMap2 实现（支持按文档删除，重解析幂等）；
TODO(可选)：接入 Qdrant/Milvus 适配器以支撑更大规模。
"""
import json
from pathlib import Path

from loguru import logger

from app.core.config import Settings


def _int_id(chunk_id: str) -> int:
    """chunk uuid(hex) 前 15 位转 int64，作为 FAISS 外部 ID。"""
    return int(chunk_id[:15], 16)


class FaissVectorStore:
    """FAISS IndexFlatIP（内积，配合归一化向量即余弦相似度）+ ID 映射 + 元数据 JSON。"""

    def __init__(self, settings: Settings):
        self.dir = settings.vector_store_dir
        self.dim = settings.embedding_dim
        self.index_file = self.dir / "index.faiss"
        self.meta_file = self.dir / "meta.json"
        self._index = None
        self._meta: dict[str, dict] = {}  # key: str(int_id)

    # ---------- 索引管理 ----------
    def _ensure_index(self):
        if self._index is None:
            import faiss  # 延迟导入

            if self.index_file.exists():
                self._index = faiss.read_index(str(self.index_file))
                self._meta = json.loads(self.meta_file.read_text(encoding="utf-8"))
            else:
                self._index = faiss.IndexIDMap2(faiss.IndexFlatIP(self.dim))
                self._meta = {}
        return self._index

    def count(self) -> int:
        return self._ensure_index().ntotal

    def upsert(self, ids: list[str], vectors: list[list[float]], metadatas: list[dict]) -> None:
        import numpy as np

        if not ids:
            return
        index = self._ensure_index()
        int_ids = np.array([_int_id(i) for i in ids], dtype="int64")
        index.add_with_ids(np.array(vectors, dtype="float32"), int_ids)
        for int_id, vec_id, meta in zip(int_ids.tolist(), ids, metadatas):
            self._meta[str(int_id)] = {**meta, "chunk_id": vec_id}
        self._save()

    def delete_document(self, document_id: str) -> int:
        """删除某文档的全部向量（重解析前调用，保证幂等）。返回删除数量。"""
        import numpy as np

        index = self._ensure_index()
        if index.ntotal == 0:
            return 0
        to_remove = [k for k, m in self._meta.items() if m.get("document_id") == document_id]
        if not to_remove:
            return 0
        index.remove_ids(np.array([int(k) for k in to_remove], dtype="int64"))
        for k in to_remove:
            self._meta.pop(k, None)
        self._save()
        return len(to_remove)

    def _save(self) -> None:
        import faiss

        self.dir.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self._index, str(self.index_file))
        self.meta_file.write_text(json.dumps(self._meta, ensure_ascii=False), encoding="utf-8")
        logger.debug(f"向量索引已保存: ntotal={self._index.ntotal}")

    # ---------- 检索 ----------
    def search(self, vector: list[float], k: int = 5,
               filter_document_ids: list[str] | None = None) -> list[dict]:
        import numpy as np

        index = self._ensure_index()
        if index.ntotal == 0:
            return []
        fetch = k * 4 if filter_document_ids else k  # 有过滤时多取一些再筛选
        scores, ids = index.search(np.array([vector], dtype="float32"), min(fetch, index.ntotal))
        results: list[dict] = []
        for score, int_id in zip(scores[0], ids[0]):
            if int_id < 0:
                continue
            meta = self._meta.get(str(int(int_id)))
            if meta is None:
                continue
            if filter_document_ids and meta.get("document_id") not in filter_document_ids:
                continue
            results.append({"id": meta["chunk_id"], "score": float(score), "metadata": meta})
            if len(results) >= k:
                break
        return results
