from sentence_transformers import SentenceTransformer, util
import torch
from pathlib import Path

ROOT_DIR = Path(__file__).parent
bge_model_path = ROOT_DIR / 'models' / 'bge-small-zh-v1.5'

model = SentenceTransformer(str(bge_model_path))

# 2. 定义数据库文本（语料库）
corpus = [
    "我喜欢机器学习",
    "我喜欢深度学习",
    "我今天心情很不错"
]

# 3. 定义查询文本
query = "我今天很开心"

# 编码
corpus_embeddings = model.encode(corpus, convert_to_tensor=True)
query_embedding = model.encode(query, convert_to_tensor=True)

# 计算相似度（使用 util 工具）
cos_scores = util.cos_sim(query_embedding, corpus_embeddings)[0]

# 获取 top-3 结果
top_results = torch.topk(cos_scores, k=min(3, len(corpus)))

print(f"查询: {query}\n")
for score, idx in zip(top_results[0], top_results[1]):
    print(f"{corpus[idx]} (相似度: {score:.4f})")

result_index = top_results[1][0]
print(f'匹配句子：{corpus[result_index]}')
