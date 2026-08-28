from sentence_transformers import SentenceTransformer
import numpy as np

model = SentenceTransformer(r"../../BAAI/bge-small-zh-v1.5")

query_text = "我今天很开心"
corpus = [
    "我喜欢机器学习",
    "我喜欢深度学习",
    "我今天心情很不错"
]

query = f"为这个句子生成表示以用于检索：{query_text}"

query_embedding = model.encode(query)
corpus_embeddings = model.encode(corpus)

def cosine_similarity(a, b):
    return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))

scores = []
for idx, emb in enumerate(corpus_embeddings):
    sim = cosine_similarity(query_embedding, emb)
    scores.append((corpus[idx], sim))

scores.sort(key=lambda x:x[1], reverse=True)

print("====检索结果（文本｜相似度分数）====")
for text, score in scores:
    print(f"{text} | {score:.4f}")