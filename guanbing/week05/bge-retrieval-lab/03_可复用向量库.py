"""
Step 3: 进阶版 - 可复用的 VectorStore 类
- 支持 add_documents / search
- 支持 top-k 返回
- 演示阈值过滤 (例如相似度 < 0.4 则判为不相关)
"""
import os
import sys
import numpy as np
import torch
from sentence_transformers import SentenceTransformer

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(PROJECT_DIR, ".cache")
os.environ["HF_HOME"] = os.path.join(CACHE_DIR, "huggingface")
os.environ["TRANSFORMERS_CACHE"] = os.path.join(CACHE_DIR, "transformers")
os.environ["SENTENCE_TRANSFORMERS_HOME"] = os.path.join(CACHE_DIR, "sentence_transformers")
os.makedirs(CACHE_DIR, exist_ok=True)

LOCAL_MODEL_DIR = os.path.join(PROJECT_DIR, "BAAI", "bge-small-zh-v1.5")

INSTRUCTION_FOR_RETRIEVAL = "为这个句子生成表示以用于检索相关文章："


class SimpleBGEVectorStore:
    def __init__(self, model_path: str, device: str = None):
        if device is None:
            device = "mps" if torch.backends.mps.is_available() else "cpu"
        self.model = SentenceTransformer(model_path, device=device)
        self.documents = []
        dim_fn = getattr(self.model, "get_embedding_dimension", None) or self.model.get_sentence_embedding_dimension
        self._dim = dim_fn() if callable(dim_fn) else dim_fn
        self.embeddings = np.empty((0, self._dim))

    @property
    def dim(self):
        return self._dim

    def add_documents(self, texts: list[str]):
        """批量添加文档到向量库"""
        if not texts:
            return
        vecs = self.model.encode(
            texts, normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False
        )
        self.documents.extend(texts)
        if self.embeddings.size == 0:
            self.embeddings = vecs
        else:
            self.embeddings = np.vstack([self.embeddings, vecs])
        print(f"➕ 已加入 {len(texts)} 条文档，当前总量: {len(self.documents)}")

    def search(self, query: str, top_k: int = 3, threshold: float = 0.0) -> list[tuple[str, float]]:
        """给定查询，返回排序后的 [(文本, 相似度), ...]"""
        query_with_inst = INSTRUCTION_FOR_RETRIEVAL + query
        qv = self.model.encode(
            query_with_inst, normalize_embeddings=True, convert_to_numpy=True
        )
        # 矩阵乘法 (1 x d) @ (d x N) -> 1 x N 相似度，已归一化直接点积就是 cosine
        scores = (self.embeddings @ qv.reshape(-1, 1)).flatten()

        idx_sorted = np.argsort(-scores)  # 降序
        results = []
        for i in idx_sorted[:top_k]:
            s = float(scores[i])
            if s < threshold:
                continue
            results.append((self.documents[i], s))
        return results


if __name__ == "__main__":
    print("=" * 60)
    print("[Step 3] BGE 检索进阶版 - SimpleBGEVectorStore")
    print("=" * 60)

    if not os.path.isdir(LOCAL_MODEL_DIR):
        print(f"❌ 请先运行 01_下载模型.py")
        sys.exit(1)

    db = SimpleBGEVectorStore(LOCAL_MODEL_DIR)
    print(f"✅ 向量库初始化，dim={db.dim}")

    # 构造数据库（比最小版多几条，模拟真实检索）
    docs = [
        "我喜欢机器学习",
        "我喜欢深度学习",
        "我今天心情很不错",
        "北京是中国的首都",
        "昨天我去了上海出差",
        "今天是个好日子，心情愉悦",
    ]
    db.add_documents(docs)

    queries = [
        ("我今天很开心", 3, 0.0),
        ("深度学习和机器学习是什么关系？", 3, 0.0),
        ("你知道上海吗？", 3, 0.0),
    ]

    for q, k, th in queries:
        print(f"\n🔍 查询: {q}  (top_k={k}, threshold={th})")
        print(f"  {'Rank':<5}{'Score':<10}{'Document'}")
        print("  " + "-" * 55)
        hits = db.search(q, top_k=k, threshold=th)
        for rank, (doc, s) in enumerate(hits, 1):
            print(f"  {rank:<5}{s:<10.4f}{doc}")
        if not hits:
            print("  （无命中，阈值过高或无相关文档）")
