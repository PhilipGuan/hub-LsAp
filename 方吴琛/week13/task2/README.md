# Chinook SQL Agent · 网页对话前端（task2）

在 task1 的 LangGraph SQL Agent 之上加一层 Web 界面：用户在网页对话框里输入
自然语言问题，后端调用同一个 `SQLAgentWorkflow.run(question)`，把
`WorkflowOutput`（答案、SQL、使用的表、行数、证据、注意事项）渲染成聊天气泡。

## 结构

```
task2/
├── server.py          # FastAPI：/api/query + 静态页面
├── static/
│   ├── index.html     # 对话页面
│   ├── style.css
│   └── app.js         # 渲染消息、调用接口
└── pyproject.toml     # 仅 fastapi / uvicorn
```

后端不重复实现 agent：`server.py` 顶部把 `../task1/src` 加入 `sys.path`，直接
`from sql_agent.graph import SQLAgentWorkflow`，共享一个实例（图只编译一次，
DB catalog / executor 只读，可并发）。

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/` | 对话页面（`static/index.html`） |
| GET | `/health` | `{"status","model","base_url","api_key_configured"}` |
| POST | `/api/query` | 入参 `{"question": "..."}`，返回 `WorkflowOutput` |

每条问题独立查询（agent 不保留多轮上下文），对话历史仅在前端展示。

## 运行

```bash
# 1. 先按 task1 的要求配置模型（与命令行一致）
export OPENAI_API_KEY=<你的 DeepSeek Key>
export OPENAI_BASE_URL=https://api.deepseek.com   # 不要带 /anthropic，否则 404

# 2. 启动服务
cd task2
python server.py            # 默认 http://127.0.0.1:8000

# 3. 浏览器打开
open http://127.0.0.1:8000
```

可选环境变量：

- `SQL_AGENT_WEB_HOST` / `SQL_AGENT_WEB_PORT`：监听地址与端口。
- `SQL_AGENT_MODEL` / `SQL_AGENT_SUMMARY_MODEL`：覆盖模型（默认 `deepseek-chat`）。
- `SQL_AGENT_LOG_LEVEL`：日志级别。

页面右上角状态灯：绿色表示 API Key 已配置，红色表示缺失或服务不可用。

## 说明

- 答案里的 Markdown 表格（如汇总 Agent 降级时输出的表格）会被渲染成 HTML 表格；
  SQL 显示在「查询详情」折叠区里，方便核对。
- 缺 `OPENAI_API_KEY` 时服务仍可启动、页面仍可打开，但发起查询会返回 500 并
  提示未配置。
