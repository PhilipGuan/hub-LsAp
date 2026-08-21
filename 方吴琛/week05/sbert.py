"""
1、 本地安装下 sentence-transformer库，使用bge模型进行文本检索，不需要es
```
modelscope download --model BAAI/bge-small-zh-v1.5  --local_dir BAAI/bge-small-zh-v1.5
```
待检索的文本：我今天很开心
数据库文本：
- 我喜欢机器学习
- 我喜欢深度学习
- 我今天心情很不错
"""

from sentence_transformers import SentenceTransformer

sentences = [
    "我喜欢机器学习",
    "我喜欢深度学习",
    "我今天心情很不错"
]

model = SentenceTransformer("bge-small-zh-v1.5/") # sentence-bert 微调之后的

embeddings = model.encode(sentences)

query = "我今天很开心"
query_embeddings = model.encode(query)

similarities = model.similarity(embeddings, query_embeddings)

max_score = 0
for i, score in enumerate(similarities):
    print(f"文本：{sentences[i]} | 相似度：{score.item():.2f}")
    if score > max_score:
        best_sentence = sentences[i]
        max_score = score

print("\n====最匹配结果====")
if max_score > 0:
    print(f"最佳匹配文本：{best_sentence} | 相似度：{score.item():.2f}")
