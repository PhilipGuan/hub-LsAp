import numpy as np
from sentence_transformers import SentenceTransformer


# 加载本地 BGE 模型
model = SentenceTransformer("../BAAI/bge-small-zh-v1.5")

# 待检索文本
query = "我今天很开心"

# 数据库文本
database_texts = [
    "我喜欢机器学习",
    "我喜欢深度学习",
    "我今天心情很不错"
]

# 将文本转换为向量
query_embedding = model.encode(
    query,
    normalize_embeddings=True
)

database_embeddings = model.encode(
    database_texts,
    normalize_embeddings=True
)

# 计算查询文本和每条数据库文本的相似度
scores = database_embeddings @ query_embedding

# 按相似度从高到低排序
ranking = np.argsort(-scores)

for rank, index in enumerate(ranking, start=1):
    print(
        f"{database_texts[index]}，"
        f"相似度：{scores[index]:.4f}"
    )

# 输出最相似的文本
best_index = ranking[0]
print("\n最相似的文本：")
print(database_texts[best_index])
