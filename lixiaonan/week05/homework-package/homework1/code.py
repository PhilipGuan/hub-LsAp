# 1. 安装依赖（在终端中执行）
# pip install sentence-transformers

# 2. 导入库并加载模型
from sentence_transformers import SentenceTransformer, util

# 加载 BGE 中文模型（首次运行会自动下载，约 1.3GB）
model_path = "/Users/lixiaonan47/Desktop/9-code/python_test/py312/model_dir/models/BAAI--bge-small-zh-v1.5/snapshots/master"
model = SentenceTransformer(model_path)

# 3. 准备数据
query = "我今天很开心"  # 待检索的文本

corpus = [
    "我喜欢机器学习",
    "我喜欢深度学习",
    "我今天心情很不错"
]

# 4. 将数据库中的所有文本转换成向量（可以提前存好，避免重复计算）
corpus_embeddings = model.encode(corpus, convert_to_tensor=True)

# 5. 将查询文本转换成向量
query_embedding = model.encode(query, convert_to_tensor=True)

# 6. 计算查询向量与所有数据库向量的余弦相似度
cos_scores = util.cos_sim(query_embedding, corpus_embeddings)[0]

# 7. 按相似度从高到低排序，输出结果
top_k = 3  # 想返回的最相似结果数量
top_results = sorted(
    [(corpus[i], cos_scores[i].item()) for i in range(len(corpus))],
    key=lambda x: x[1],
    reverse=True
)

# 8. 打印结果
print(f"查询语句: {query}\n")
print("最相似的文本:")
for text, score in top_results:
    print(f"  {text}  (相似度: {score:.4f})")


