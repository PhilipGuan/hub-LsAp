from  sentence_transformers import SentenceTransformer

# 加载bge模型
model = SentenceTransformer("/课程/00.assets/models/BAAI/bge-small-zh-v1.5", device="cpu")

# 文本列表
txt_list = [
    "我喜欢机器学习",
    "我喜欢深度学习",
    "我今天心情很不错"
]
print("数据库本列表：")
for i, t in enumerate(txt_list):
    print(f"\t{i+1}. {t}")

# 编码文本列表
embeddings = model.encode(txt_list, show_progress_bar=False)
# 将索引和文本映射到字典中
idx2txt ={i: t for i, t in enumerate(txt_list)}
# 将索引和嵌入向量映射到字典中
idx2embeddings = {i: e for i, e in enumerate(embeddings)}

# 输入文本
input_txt = "我今天很开心"
# 编码输入文本
input_txt_embedding = model.encode(input_txt, show_progress_bar=False)
print(f"待检索的文本：{input_txt}")

# 计算相似度
similarities = model.similarity(embeddings, input_txt_embedding)
# 打印相似度
print(f"计算相似度：\n\t{similarities}")

# 遍历相似度列表，找到相似度大于0.8的文本，并根据索引召回原始文本，并添加到列表中
similar_txt_list = [idx2txt[i] for i, s in enumerate(similarities) if s[0].item() > 0.8]
print(f"相似大于0.8的文本列表：{similar_txt_list}")