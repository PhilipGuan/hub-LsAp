# 第五周作业

## 作业1：BGE 文本检索

- 本地模型：`BAAI/bge-small-zh-v1.5`
- 实现：Sentence Transformers + 归一化向量余弦相似度
- 不使用 Elasticsearch
- 运行结果保存在 `task1_bge/retrieval_result.json`

## 作业2：Ollama SDK 调用

- 本地模型：`qwen3:0.6b`
- 接口：Ollama 的 OpenAI 兼容地址 `http://localhost:11434/v1`
- SDK：OpenAI Python SDK 的 `client.chat.completions.create()`

模型文件单独放在 `week05/models`，不放入作业提交目录，避免提交文件过大。
