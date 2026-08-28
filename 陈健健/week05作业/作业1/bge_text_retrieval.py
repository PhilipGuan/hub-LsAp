# -*- coding: utf-8 -*-
"""
使用 sentence-transformers + BGE 模型进行文本检索。

运行前准备（一次性）：
1. 安装依赖：
   pip install sentence-transformers
   pip install modelscope
2. 下载 BGE 模型到本地（在当前目录生成 BAAI/bge-small-zh-v1.5 文件夹）：
   modelscope download --model BAAI/bge-small-zh-v1.5 --local_dir BAAI/bge-small-zh-v1.5
"""

from sentence_transformers import SentenceTransformer

# 模型本地路径（由 modelscope 下载命令生成）
MODEL_PATH = "BAAI/bge-small-zh-v1.5"

# 待检索文本（查询）
query = "我今天很开心"

# 数据库文本（语料库）
passages = [
    "我喜欢机器学习",
    "我喜欢深度学习",
    "我今天心情很不错",
]

# BGE 中文模型官方推荐的检索指令：仅对 query 添加，passage 不加
QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："


def main():
    # 加载本地模型
    model = SentenceTransformer(MODEL_PATH)

    # 编码：query 加检索指令，passages 不加；归一化后点积即余弦相似度
    query_embedding = model.encode(
        [QUERY_INSTRUCTION + query], normalize_embeddings=True
    )
    passage_embeddings = model.encode(passages, normalize_embeddings=True)

    # 计算余弦相似度
    similarities = (query_embedding @ passage_embeddings.T)[0]

    # 按相似度从高到低排序输出
    ranked_idx = similarities.argsort()[::-1]
    print(f"待检索文本：{query}\n")
    for rank, idx in enumerate(ranked_idx, start=1):
        print(f"{rank}. 相似度 {similarities[idx]:.4f}  ->  {passages[idx]}")


if __name__ == "__main__":
    main()
