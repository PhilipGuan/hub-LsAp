# -*- coding: utf-8 -*-
"""
==============================================================================
【Week05 - 作业】使用 BGE 模型进行文本检索
==============================================================================
【任务描述】
    待检索文本（查询 query）：我今天很开心
    数据库文本（候选文档 documents）：
        1. 我喜欢机器学习
        2. 我喜欢深度学习
        3. 我今天心情很不错
    要求：用 BGE 模型把每句话编码成句向量，计算查询与每条候选文本的
          余弦相似度，按相似度从高到低排序，输出检索结果（Top-K）。

【整体思路流程】（对应课上讲的"SBERT 检索三步走"）
    第一步：加载本地 BGE 模型（已下载在 D:/Study/26/BAAI/bge-small-zh-v1.5）
    第二步：离线预计算——把数据库文本编码成向量
            （真实检索系统里这一步提前做好缓存，线上不用重算）
    第三步：在线检索——编码查询 -> 与库中每个向量算余弦相似度 -> 排序取 Top-K

【核心知识点 1：BGE 是什么？】
    bge-small-zh-v1.5：智源(BAAI)开源的中文句向量模型，
    512 维向量，专门用对比学习微调过"句向量"能力（MTEB 榜单同规模领先）。
    sentence-transformers 可以直接加载。

【核心知识点 2：检索为什么用余弦相似度？】
    cos(A, B) = A·B / (|A||B|)，只看向量"方向"是否一致，不看长度。
    值域 [-1, 1]：越接近 1 语义越相似，接近 0 语义无关。

【预期结果】
    "我今天心情很不错"与"我今天很开心"语义最接近 -> 相似度应排第一；
    机器学习/深度学习两句与查询语义无关 -> 相似度明显更低。
==============================================================================
"""

import sys

from sentence_transformers import SentenceTransformer

# Windows 控制台默认 GBK 编码，强制 UTF-8 避免中文输出乱码
sys.stdout.reconfigure(encoding="utf-8")

# ============================== 第一步：加载本地 BGE 模型 ==============================
# 模型已提前下载到本地：D:/Study/26/BAAI/bge-small-zh-v1.5
# 下载命令：modelscope download --model BAAI/bge-small-zh-v1.5 --local_dir D:/Study/26/BAAI/bge-small-zh-v1.5
MODEL_PATH = "D:/Study/26/BAAI/bge-small-zh-v1.5"

# SentenceTransformer 封装了 分词 -> BERT 前向 -> MeanPooling 全流程
model = SentenceTransformer(MODEL_PATH)

# 待检索文本（用户查询）
query = "我今天很开心"

# 数据库文本（候选文档）
documents = [
    "我喜欢机器学习",
    "我喜欢深度学习",
    "我今天心情很不错",
]

# ============================== 第二步：离线预计算文档向量 ==============================
# 数据库文本 -> 句向量，形状 (3, 512)：每条文档一个 512 维向量
# 真实系统里这一步离线完成并缓存（向量库），线上检索不需要重算
doc_embeddings = model.encode(documents)
print(f"文档向量矩阵形状: {doc_embeddings.shape}")

# ============================== 第三步：在线检索 ==============================
# 查询 -> 句向量，形状 (512,)
query_embedding = model.encode(query)

# 计算查询与每条文档的余弦相似度，得到相似度矩阵 (1, 3)
# model.similarity 内部就是 cos(A, B) = A·B / (|A||B|)
similarities = model.similarity(query_embedding, doc_embeddings)
print(f"\n查询文本: {query}")
print("-" * 50)
for doc, score in zip(documents, similarities[0]):
    print(f"相似度: {score.item():.4f} | {doc}")

# 按相似度从高到低排序，输出 Top-K 检索结果
print("-" * 50)
top_k = 3
scores = similarities[0]
# argsort 降序拿到排序后的文档下标
ranked_indices = scores.argsort(descending=True)[:top_k]

print(f"检索结果 Top-{top_k}:")
for rank, idx in enumerate(ranked_indices, start=1):
    print(f"第{rank}名: {documents[idx.item()]} (相似度: {scores[idx].item():.4f})")

# 【观察点】
# 1. "我今天心情很不错"与查询语义最接近 -> 排第一，说明 BGE 能理解"语义"
#    而不是只看字面（字面上它和查询的公共字反而不如"我喜欢机器学习"多）
# 2. 若换成原始 bert-base-chinese 编码，排序结果会明显变差（课上已对比过）
# 3. 文档向量可提前缓存 -> 检索时只需 1 次查询编码 + 向量点积，这就是 RAG 检索的基础
