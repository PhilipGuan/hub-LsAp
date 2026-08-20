from sentence_transformers import SentenceTransformer

model = SentenceTransformer(
    "e:/csg/temuspider/python/agent学习/root/Week2-大模型使用与深度学习基础/课程资料/Week02_2026Q2/asserts/bge-small-zh-v1.5"
)

# 待检索的文本（用户提问）
query = "我今天很开心"

# 数据库文本（文本库）
corpus = [
    "我喜欢机器学习",
    "我喜欢深度学习",
    "我今天心情很不错",
]

# 分别编码：query 和 corpus 各自独立提取向量（SBERT 的核心优势）
query_emb = model.encode(query)      # shape: (512,)
corpus_emb = model.encode(corpus)    # shape: (3, 512)

# 计算 query 与每个候选句的相似度
sims = model.similarity(query_emb, corpus_emb)[0]  # shape: (3,)

print("query vs 每个候选句的相似度：")
for text, s in zip(corpus, sims):
    print(f"  {s:.4f}  「{text}」")

best_idx = int(sims.argmax())
print(f"\n最相似的是：{corpus[best_idx]}")
