from sentence_transformers import SentenceTransformer

sentences = [
    "我喜欢机器学习",
    "我喜欢深度学习",
    "我今天心情很不错"
    ]

sentence = "我今天很开心"

model = SentenceTransformer("D:/AIwork/models/BAAI/bge-small-zh-v1.5/")

# 将数据库文本与待检索文本分别编码
query_embedding = model.encode([sentence])
corpus_embeddings = model.encode(sentences)

# 计算目标句与每个候选句的相似度
similarities = model.similarity(query_embedding, corpus_embeddings)[0]
# print(similarities)



# 找出相似度最高的文本
best_idx = int(similarities.argmax())
best_score = float(similarities[best_idx])
# print(best_score)

print(f"相似度最高的文本: {sentences[best_idx]}, 相似度: {best_score:.4f}")