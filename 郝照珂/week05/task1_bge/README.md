# 作业1：BGE 文本检索

使用 `sentence-transformers` 加载本地 `BAAI/bge-small-zh-v1.5`，将查询与数据库文本编码为归一化向量，再用向量点积计算余弦相似度，不需要 Elasticsearch。

```powershell
C:\Users\haozh\anaconda3\envs\pytorch\python.exe bge_text_retrieval.py
```

预期第一名是“我今天心情很不错”，因为它与“我今天很开心”的语义最接近。
