from sentence_transformers import SentenceTransformer
import numpy as np

# 加载本地 BGE 模型
model = SentenceTransformer(
    "../BAAI/bge-small-zh-v1.5"
)

# 数据库中的文本
documents = [
    "我喜欢机器学习",
    "我喜欢深度学习",
    "我今天心情很不错",
]

# 查询
query = "我今天很开心"

# 计算数据库文本的 embedding
doc_embeddings = model.encode(
    documents,
    normalize_embeddings=True
)

# 计算 query embedding
query_embedding = model.encode(
    query,
    normalize_embeddings=True
)

# cosine similarity
scores = np.dot(doc_embeddings, query_embedding)

# 按相似度从高到低排序
results = sorted(
    zip(documents, scores),
    key=lambda x: x[1],
    reverse=True
)

for text, score in results:
    print(f"{score:.4f}  {text}")
