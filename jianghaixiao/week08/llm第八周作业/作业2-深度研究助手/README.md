# 深度研究助手

这是第八周作业 2 的独立实现。输入一个研究主题，后端自动完成：

```text
关键词规划 -> 网页搜索 -> 内容总结 -> 判断补检 -> 生成报告 -> 保存过程
```

默认使用 mock 搜索和 mock 文案，没有 API Key 也可以完整运行。配置
`OPENAI_API_KEY` 和 `BOCHA_API_KEY` 后，会优先调用真实模型和搜索 API。

## 技术栈

- Python 3.10+
- FastAPI
- Pydantic v2
- httpx
- 本地 JSON 文件存储

## 快速开始

```powershell
python -m pip install -r requirements.txt
python -m uvicorn backend.app:app --reload --port 8010
```

浏览器打开接口文档：

```text
http://127.0.0.1:8010/docs
```

发起研究：

```powershell
curl -X POST http://127.0.0.1:8010/api/research `
  -H "Content-Type: application/json" `
  -d "{\"topic\":\"2026 年主流 Agent 框架对比\"}"
```

返回 `research_id` 后轮询：

```powershell
curl http://127.0.0.1:8010/api/research/<research_id>
```

状态会从 `pending` 变为 `running`，最终变为 `completed`。

## 环境变量

复制 `.env.example` 为 `.env`：

```text
USE_MOCK=auto
OPENAI_API_KEY=
OPENAI_BASE_URL=https://api.deepseek.com/
MODEL_NAME=deepseek-chat
BOCHA_API_KEY=
BOCHA_SEARCH_COUNT=5
MAX_ROUNDS=2
```

`USE_MOCK=auto` 时，只要模型 Key 或搜索 Key 缺失，就自动使用 mock，
保证演示流程稳定。真实模式下会调用 OpenAI 兼容的 chat completions 接口
和 Bocha 网页搜索接口。

## 目录结构

```text
backend/
├── app.py          FastAPI 接口
├── config.py       环境配置
├── engine.py       研究流程编排
├── llm.py          模型调用与无模型降级
├── models.py       Pydantic 数据结构
├── prompts.py      提示词模板
├── research.py     后台任务和逐步落盘
├── storage.py      JSON 文件存储
└── tools.py        搜索工具
```

运行时数据写入：

```text
backend/data/research/<research_id>.json
```

## 接口

```text
GET  /health
POST /api/research
GET  /api/research
GET  /api/research/{research_id}
GET  /api/research/{research_id}/html
```

## 验证

```powershell
python -m pytest
python -m compileall backend
```

若没有 Key，仍应能完成一次 mock 研究并得到 `completed` 记录。
