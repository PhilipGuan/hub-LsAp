import os
from pageindex import PageIndexClient

# 设置环境变量
os.environ["OPENAI_API_KEY"] = "sk-ws-H.EDPMPHI.68lp.MEUCIH8GSCCfGY4Hr1QfMhUBb-wdjDri7u5NndaJJO-j2xs9AiEA7edSjfdo4U3OGeBjeBhKENxjkpcsdy0UA7rYr97gJJY"
os.environ["OPENAI_BASE_URL"] = "https://ws-09qg8sou349yp1mg.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"

client = PageIndexClient(
    index_model="openai/qwen-flash",  # 构建 tree 使用的模型（字符串）
    chat_model="openai/qwen-flash",   # 对话时候使用的模型（字符串）
    storage_path=".pageindex",
)

# 建立 PageIndex
result = client.submit_document(
    "资料/2404.16130v2-GraphRAG.pdf"
)

doc_id = result["doc_id"]
print("doc_id:", doc_id)

# 查看 Tree Index
tree = client.get_document_structure(doc_id)
print(f'文档结构：{tree}')

# 基于 PageIndex 问答
answer = client.chat(
    "这份文档主要讲了什么？",
    doc_id=doc_id,
    # reasoning_effort="high",
)
print(answer)