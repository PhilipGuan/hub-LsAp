from sentence_transformers import SentenceTransformer
import numpy as np
from typing import List, Tuple
import os


class TextRetriever:
    def __init__(self, model_path: str = "asserts/bge-small-zh-v1.5"):
        """
        初始化文本检索器
        Args:
            model_path: 本地模型路径
        """
        # 检查模型路径是否存在
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"模型路径不存在: {model_path}")

        # 加载本地模型
        self.model = SentenceTransformer(model_path)
        self.documents = []
        self.embeddings = None

    def add_documents(self, documents: List[str]):
        """
        添加文档到检索库
        Args:
            documents: 文档列表
        """
        self.documents = documents
        # 编码所有文档
        self.embeddings = self.model.encode(
            documents,
            convert_to_numpy=True,
            normalize_embeddings=True  # BGE模型推荐归一化
        )

    def search(self, query: str, top_k: int = 3) -> List[Tuple[str, float]]:
        """
        检索最相关的文档
        Args:
            query: 查询文本
            top_k: 返回前k个结果
        Returns:
            包含(文档, 相似度分数)的列表
        """
        if self.embeddings is None:
            raise ValueError("请先添加文档到检索库")

        # 编码查询文本
        query_embedding = self.model.encode(
            query,
            convert_to_numpy=True,
            normalize_embeddings=True
        )

        # 计算余弦相似度
        similarities = np.dot(self.embeddings, query_embedding)

        # 获取top_k结果
        top_indices = np.argsort(similarities)[-top_k:][::-1]

        results = []
        for idx in top_indices:
            results.append((self.documents[idx], float(similarities[idx])))

        return results


# 使用示例
if __name__ == "__main__":
    # 初始化检索器
    retriever = TextRetriever("asserts/bge-small-zh-v1.5")

    # 数据库文本
    database_texts = [
        "我喜欢机器学习",
        "我喜欢深度学习",
        "我今天心情很不错"
    ]

    # 添加文档到检索库
    retriever.add_documents(database_texts)

    # 待检索文本
    query = "我今天很开心"

    # 执行检索
    results = retriever.search(query, top_k=3)

    print(f"查询: {query}")
    print("检索结果:")
    for doc, score in results:
        print(f"  相似度: {score:.4f} | 文本: {doc}")