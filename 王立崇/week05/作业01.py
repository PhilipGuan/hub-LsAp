from sentence_transformers import SentenceTransformer
import numpy as np

# 1. 加载模型（会自动从本地或缓存加载）
# 如果本地有下载好的，可以直接指定路径，比如 model = SentenceTransformer("./BAAI/bge-small-zh-v1.5")
model = SentenceTransformer("BAAI/bge-small-zh-v1.5") 

# 2. 准备数据
query = "学习"  # 待检索的文本
corpus = [            # 数据库中的文本列表
    "我喜欢机器学习",
    "我喜欢深度学习",
    "我今天心情很不错"
]

# 3. 将数据库中的所有文本编码成向量（这一步可以离线提前完成）
corpus_embeddings = model.encode(corpus)

# 4. 将查询文本也编码成向量
query_embedding = model.encode([query])[0] # encode默认返回数组，取第一个

# 5. 计算查询向量与所有数据库向量的余弦相似度
similarities = model.similarity(query_embedding, corpus_embeddings)[0] 
# model.similarity 返回一个矩阵，我们取第一行

# 6. 对结果进行排序，得到最相关的文本
# 将相似度得分和对应的文本、索引组合在一起
results = sorted(
    [(score, corpus[idx]) for idx, score in enumerate(similarities)], 
    reverse=True, 
    key=lambda x: x[0]
)

# 7. 打印结果
print(f"查询：{query}\n")
print("检索结果（按相似度从高到低排列）：")
for score, text in results:
    print(f"  得分：{score:.4f} \t 文本：{text}")