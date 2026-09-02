"""
模块四：混合检索模块
BM25 关键词检索 + 向量语义检索 + Neo4j 图谱查询，
通过 RRF（Reciprocal Rank Fusion）算法融合排序
"""

import jieba
import numpy as np
import faiss
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer
from typing import List, Dict, Tuple, Optional
from collections import defaultdict

from config import (
    EMBEDDING_MODEL_NAME,
    BM25_TOP_K,
    VECTOR_TOP_K,
    FINAL_TOP_K,
    RRF_K,
)


# ─────────────────────────────────────────────────────────
# 电商领域知识库（示例文档，实际从 DB 加载）
# ─────────────────────────────────────────────────────────
KNOWLEDGE_DOCS = [
    {"id": "doc_001", "content": "耐克（Nike）品牌运动鞋支持7天无理由退换货，退货商品需保持完好无损，附带原包装及发票。"},
    {"id": "doc_002", "content": "AirMax运动鞋价格区间为500-1500元，提供黑色、白色、红色三种颜色选择。"},
    {"id": "doc_003", "content": "Air Force 1 系列价格区间为600-900元，适合日常穿搭与轻量运动。"},
    {"id": "doc_004", "content": "苹果（Apple）iPhone 15 支持5G网络，价格区间5999-7999元，提供14天无理由退换货服务。"},
    {"id": "doc_005", "content": "双十一活动时间为2024年11月1日至11月11日，满减优惠：每满300元减30元，最高可减150元。"},
    {"id": "doc_006", "content": "退款处理时效：申请通过后3个工作日内完成退款，退回原支付账户。"},
    {"id": "doc_007", "content": "物流查询：可在订单详情页查看实时物流轨迹，一般72小时内完成发货。"},
    {"id": "doc_008", "content": "优惠券使用规则：优惠券不可叠加使用，仅限指定商品，有效期30天。"},
    {"id": "doc_009", "content": "商品保修政策：电子产品享受1年官方保修，保修期内免费维修或更换同款产品。"},
    {"id": "doc_010", "content": "投诉渠道：可通过客服热线400-xxx-xxxx、在线客服或官网投诉通道提交投诉，24小时内响应。"},
]


# ─────────────────────────────────────────────────────────
# 1. BM25 检索器
# ─────────────────────────────────────────────────────────
class BM25Retriever:
    """
    基于 BM25Okapi 的关键词稀疏检索
    使用 jieba 对中文文本分词
    """

    def __init__(self, documents: List[Dict] = None):
        self.documents = documents or KNOWLEDGE_DOCS
        self._build_index()

    def _tokenize(self, text: str) -> List[str]:
        """jieba 分词"""
        return list(jieba.cut(text))

    def _build_index(self):
        """构建 BM25 索引"""
        corpus = [self._tokenize(doc["content"]) for doc in self.documents]
        self.bm25 = BM25Okapi(corpus)
        print(f"✓ BM25 索引构建完成，文档数：{len(self.documents)}")

    def search(self, query: str, top_k: int = BM25_TOP_K) -> List[Dict]:
        """
        Returns:
            [{"id": ..., "content": ..., "score": ...}, ...]
        """
        tokens = self._tokenize(query)
        scores = self.bm25.get_scores(tokens)

        top_indices = np.argsort(scores)[::-1][:top_k]
        results = []
        for idx in top_indices:
            if scores[idx] > 0:
                results.append({
                    **self.documents[idx],
                    "score": float(scores[idx]),
                    "source": "bm25",
                })
        return results


# ─────────────────────────────────────────────────────────
# 2. 向量检索器（FAISS）
# ─────────────────────────────────────────────────────────
class VectorRetriever:
    """
    基于 Sentence-BERT + FAISS 的向量语义检索
    """

    def __init__(
        self,
        documents: List[Dict] = None,
        model_name: str = EMBEDDING_MODEL_NAME,
    ):
        self.documents = documents or KNOWLEDGE_DOCS
        print(f"加载向量编码模型：{model_name} ...")
        self.encoder = SentenceTransformer(model_name)
        self.dim = self.encoder.get_sentence_embedding_dimension()
        self._build_index()

    def _build_index(self):
        """编码所有文档并构建 FAISS 索引"""
        texts = [doc["content"] for doc in self.documents]
        embeddings = self.encoder.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        embeddings = np.array(embeddings, dtype=np.float32)

        # 内积索引（归一化后等价余弦相似度）
        self.index = faiss.IndexFlatIP(self.dim)
        self.index.add(embeddings)
        print(f"✓ FAISS 向量索引构建完成，向量维度：{self.dim}，文档数：{self.index.ntotal}")

    def encode_query(self, query: str) -> np.ndarray:
        """对查询文本编码"""
        vec = self.encoder.encode([query], normalize_embeddings=True)
        return np.array(vec, dtype=np.float32)

    def search(self, query: str, top_k: int = VECTOR_TOP_K) -> List[Dict]:
        """
        Returns:
            [{"id": ..., "content": ..., "score": ...}, ...]
        """
        query_vec = self.encode_query(query)
        scores, indices = self.index.search(query_vec, top_k)

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx >= 0:
                results.append({
                    **self.documents[idx],
                    "score": float(score),
                    "source": "vector",
                })
        return results


# ─────────────────────────────────────────────────────────
# 3. RRF 融合算法
# ─────────────────────────────────────────────────────────
def reciprocal_rank_fusion(
    result_lists: List[List[Dict]],
    k: int = RRF_K,
) -> List[Dict]:
    """
    Reciprocal Rank Fusion（倒数排名融合）

    公式：RRF(d) = Σ 1 / (k + rank_i(d))
    其中 rank_i(d) 为文档 d 在第 i 路检索结果中的排名（1-based）

    Args:
        result_lists: 多路检索结果列表
        k: 平滑常数（默认60）
    Returns:
        按 RRF 分数降序排列的文档列表
    """
    doc_scores: Dict[str, float] = defaultdict(float)
    doc_objects: Dict[str, Dict] = {}

    for result_list in result_lists:
        for rank, doc in enumerate(result_list, start=1):
            doc_id = doc["id"]
            doc_scores[doc_id] += 1.0 / (k + rank)
            if doc_id not in doc_objects:
                doc_objects[doc_id] = doc

    # 按 RRF 分数降序排列
    sorted_ids = sorted(doc_scores, key=lambda x: doc_scores[x], reverse=True)
    return [
        {**doc_objects[doc_id], "rrf_score": round(doc_scores[doc_id], 6)}
        for doc_id in sorted_ids
    ]


# ─────────────────────────────────────────────────────────
# 4. 混合检索器（整合三路）
# ─────────────────────────────────────────────────────────
class HybridRetriever:
    """
    混合检索器：BM25 + 向量检索 + 知识图谱查询
    使用 RRF 算法融合三路结果
    """

    def __init__(
        self,
        documents: List[Dict] = None,
        kg=None,               # KnowledgeGraphBuilder 或 MockKnowledgeGraph
    ):
        self.documents = documents or KNOWLEDGE_DOCS
        self.bm25_retriever = BM25Retriever(self.documents)
        self.vector_retriever = VectorRetriever(self.documents)
        self.kg = kg           # 可选，若为 None 则跳过图谱查询

    def retrieve(
        self,
        query: str,
        intent: str = "",
        entities: Dict[str, List[str]] = None,
        top_k: int = FINAL_TOP_K,
    ) -> List[Dict]:
        """
        混合检索主入口

        Args:
            query:    用户原始问题
            intent:   意图类别（用于图谱查询优化）
            entities: NER 识别的实体（用于图谱查询）
            top_k:    最终返回的文档数量
        Returns:
            融合排序后的知识文档列表
        """
        entities = entities or {}

        # ── 路径1：BM25 关键词检索 ──
        bm25_results = self.bm25_retriever.search(query, top_k=BM25_TOP_K)

        # ── 路径2：向量语义检索 ──
        vector_results = self.vector_retriever.search(query, top_k=VECTOR_TOP_K)

        # ── 路径3：Neo4j 知识图谱查询（可选）──
        graph_results = []
        if self.kg and entities:
            kg_texts = self.kg.query_for_intent(intent, entities)
            # 将图谱查询结果包装为 doc 格式
            graph_results = [
                {"id": f"kg_{i}", "content": text, "score": 1.0, "source": "graph"}
                for i, text in enumerate(kg_texts)
            ]

        # ── RRF 融合排序 ──
        result_lists = [bm25_results, vector_results]
        if graph_results:
            result_lists.append(graph_results)

        fused = reciprocal_rank_fusion(result_lists, k=RRF_K)
        return fused[:top_k]

    def format_context(self, docs: List[Dict]) -> str:
        """将检索到的文档拼接为 RAG 上下文字符串"""
        return "\n".join(
            f"[{i+1}] {doc['content']}" for i, doc in enumerate(docs)
        )


# ─────────────────────────────────────────────────────────
# Demo
# ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 50)
    print("混合检索模块演示")
    print("=" * 50)

    retriever = HybridRetriever()

    test_queries = [
        ("我想退一双耐克运动鞋", "INT_002_退换货咨询", {"BRAND": ["耐克"]}),
        ("iPhone 15 支持5G吗", "INT_003_商品参数查询", {"PRODUCT": ["iPhone 15"]}),
        ("双十一有什么优惠", "INT_004_优惠活动查询", {}),
    ]

    for query, intent, entities in test_queries:
        print(f"\n查询：{query}")
        print(f"意图：{intent}")
        results = retriever.retrieve(query, intent=intent, entities=entities)
        print("检索结果：")
        for i, doc in enumerate(results, 1):
            print(f"  [{i}] (rrf={doc.get('rrf_score', 0):.4f}) {doc['content'][:50]}...")
