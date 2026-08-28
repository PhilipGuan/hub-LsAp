"""
使用 sentence-transformers + 本地 BGE 中文模型进行文本检索
不依赖 Elasticsearch，纯向量相似度检索
"""
import sys
import io
# 强制 stdout 使用 UTF-8（Windows 控制台默认 GBK 会乱码）
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from sentence_transformers import SentenceTransformer
import numpy as np

# 1. 加载本地 BGE 中文模型（绝对路径，避免联网）
MODEL_PATH = r".\BAAI\bge-small-zh-v1.5"
model = SentenceTransformer(MODEL_PATH)
print(f"模型加载完成: {MODEL_PATH}")
print(f"向量维度: {model.get_sentence_embedding_dimension()}\n")

# 2. 准备语料库
corpus = [
    "我喜欢机器学习",
    "我喜欢深度学习",
    "我今天心情很不错",
]

# 3. BGE 推荐: 检索 query 时加指令前缀，文档侧不加
# 这是 bge-small-zh-v1.5 官方推荐的 query instruction
QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："

query = "我今天很开心"

# 4. 编码（BGE 内部已做 L2 归一化，dot product == cosine similarity）
print("=" * 60)
print(f"Query : {query}")
print(f"Corpus: {corpus}")
print("=" * 60)

query_emb = model.encode(QUERY_INSTRUCTION + query, normalize_embeddings=True)
doc_embs = model.encode(corpus, normalize_embeddings=True)

# 5. 计算相似度（点积 = 余弦，因为已经归一化）
scores = np.dot(doc_embs, query_emb)

# 6. 排序输出
ranked = sorted(zip(corpus, scores), key=lambda x: x[1], reverse=True)

print("\n【检索结果】（按相似度从高到低）")
print("-" * 60)
for i, (text, score) in enumerate(ranked, 1):
    bar = "█" * int(score * 30)  # 可视化条
    print(f"Top{i}  score={score:.4f}  {bar}")
    print(f"        文本: {text}")
    print()
