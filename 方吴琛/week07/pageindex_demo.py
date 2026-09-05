# 安装pageindex本地运行构建和问答过程。
# pip install -U pageindex
import os
from pageindex import PageIndexClient

# 调用环境变量
client = PageIndexClient(
    index_model="deepseek/deepseek-v4-pro", # 构建tree 使用的模型
    chat_model="deepseek/deepseek-v4-pro", # 对话时候 使用的模型
    storage_path=".pageindex",
)


# 建立 PageIndex，离线解析，对于每个文档只需要运行一次
result = client.submit_document(
    "2404.16130v2-GraphRAG.pdf"
)

doc_id = result["doc_id"]

print("doc_id:", doc_id)


# 查看 Tree Index
tree = client.get_document_structure(
    doc_id
)

print(tree)


# 基于 PageIndex 问答
answer = client.chat(
    "这份文档主要讲了什么？",
    doc_id=doc_id,
    reasoning_effort="high",
)

print(answer)
