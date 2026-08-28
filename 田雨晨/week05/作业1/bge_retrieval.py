from sentence_transformers import SentenceTransformer

MODEL_PATH = r"D:\Users\24861\Desktop\AI\models\bge-small-zh-v1.5"

query = "我今天很开心"
corpus = [
    "我喜欢机器学习",
    "我喜欢深度学习",
    "我今天心情很不错",
]

# BGE 中文检索模型需要给 query 加指令前缀
QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："

model = SentenceTransformer(MODEL_PATH)

corpus_embeddings = model.encode(corpus, normalize_embeddings=True)
query_embedding = model.encode(
    QUERY_INSTRUCTION + query, normalize_embeddings=True
)

similarities = model.similarity(query_embedding, corpus_embeddings)[0]

ranked = sorted(
    zip(range(len(corpus)), similarities),
    key=lambda x: x[1],
    reverse=True,
)

print(f"查询: {query}\n")
print("检索结果（按相似度降序）:")
for rank, (idx, score) in enumerate(ranked, start=1):
    print(f"  {rank}. [{score:.4f}] {corpus[idx]}")
