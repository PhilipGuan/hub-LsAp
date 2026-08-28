from sentence_transformers import SentenceTransformer

model = SentenceTransformer('../models/BAAI/bge-small-zh-v1.5')


sentences = [
    "我喜欢机器学习",
    "我喜欢深度学习",
    "我今天心情很不错"
]
input_sentence= "我今天很开心"
embeddings = model.encode(sentences)
in_embeddings = model.encode(input_sentence)
similarity = model.similarity(embeddings, in_embeddings)
print(similarity)