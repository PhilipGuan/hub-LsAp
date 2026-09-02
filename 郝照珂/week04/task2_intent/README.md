# 作业2：Vibe Coding 车载意图识别

这是一个从需求到接口、分类器和测试的最小完整项目。当前实现规则基线，接口层与模型层解耦，后续可把 `IntentClassifier` 替换为 TF-IDF 或 BERT。

## 运行

```powershell
pip install -r requirements.txt
uvicorn app.main:app --reload
pytest -q
```

访问 `http://127.0.0.1:8000/docs` 调试接口。

请求示例：`{"request_id":"001","text":"导航到最近的加油站"}`。
