# -*- coding: utf-8 -*-
"""bge-small-zh-v1.5 本地文本检索：query 与文档库做余弦相似度排序。"""

import os

from sentence_transformers import SentenceTransformer

# 模型已在本地下载，直接指向 Week05 里的完整模型目录
MODEL_PATH = r"C:\Users\江海啸\Desktop\江海啸\c语言笔记\python_study\llm笔记\第5周：BERT模型进阶与大模型基础\Week05\models\BAAI\bge-small-zh-v1.5"

# bge 中文检索：query 加指令，文档不需要加
QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："

QUERY = "我今天很开心"
DOCS = [
    "我喜欢机器学习",
    "我喜欢深度学习",
    "我今天心情很不错",
]


def main():
    if not os.path.isdir(MODEL_PATH):
        raise SystemExit(f"[错误] 模型目录不存在: {MODEL_PATH}")

    print("[1/3] 加载模型:", MODEL_PATH)
    model = SentenceTransformer(MODEL_PATH)

    print("[2/3] 编码向量...")
    query_vec = model.encode([QUERY_INSTRUCTION + QUERY], normalize_embeddings=True)
    doc_vecs = model.encode(DOCS, normalize_embeddings=True)

    print("[3/3] 计算余弦相似度...")
    scores = (doc_vecs @ query_vec.T).flatten()

    print(f"\n待检索文本: {QUERY}\n")
    for text, score in sorted(zip(DOCS, scores), key=lambda x: -x[1]):
        print(f"  {score:.4f}  {text}")

    print(f"\n最相似: {DOCS[scores.argmax()]}")


if __name__ == "__main__":
    main()
