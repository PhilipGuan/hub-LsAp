# 作业2：Ollama + Qwen3 0.6B SDK 调用

```powershell
ollama pull qwen3:0.6b
C:\Users\haozh\anaconda3\envs\pytorch\python.exe ollama_openai_sdk.py
```

代码使用 OpenAI Python SDK 的 Chat Completions 客户端，但 `base_url` 指向本地 Ollama，因此不消耗 OpenAI API 额度，也不需要真实 API Key。

## 本机实测

- Ollama：0.32.13
- 模型文件：522 MB
- 运行设备：RTX 4060 Laptop GPU（`ollama ps` 显示 100% GPU）
- 上下文长度：4096
- SDK 返回：`我是专注于AI领域的助手，能提供帮助与支持。`
