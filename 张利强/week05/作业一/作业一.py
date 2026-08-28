from sentence_transformers import SentenceTransformer

def get_similarity(query, sentences, model, top_k=3):
    query_embedding = model.encode(query)
    sentences_enbeddings = model.encode(sentences)
    # 计算相似度
    similarities = model.similarity(query_embedding, sentences_enbeddings)

    # 取出相似度最高的top_k个
    scores = similarities[0].cpu().numpy()
    top_k_indices = scores.argsort()[::-1][:top_k]

    result = []
    for i in top_k_indices:
        result.append({
            "text": sentences[i],
            "score": float(scores[i])
        })
    return result


model = SentenceTransformer("../../../../../../Downloads/八斗AI/Week02-大模型使用与深度学习基础/课程资料/Week02_2026Q2/asserts/bge-small-zh-v1.5/")

query = "我今天很开心"

sentences = [
    "我喜欢机器学习",
    "我喜欢深度学习",
    "我今天心情很不错"
]

result = get_similarity(query, sentences, model)
print(result)