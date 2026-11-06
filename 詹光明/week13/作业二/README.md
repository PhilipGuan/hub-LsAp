# week13 作业二：SQL Agent 网页对话前端

为 `week13/作业一` 的 LangGraph SQL Agent 提供一个网页对话界面：用户在输入框里用自然语言
提问，页面实时展示「选表 → 生成 SQL → 校验执行 → （失败改写）→ 汇总」的每一步，
最后给出答案、SQL、结果行数和执行依据。

后端**直接复用** `../作业一` 的 `sql_agent` 包，不复制、不修改原有代码；本作业只新增
Web 服务层（FastAPI）与前端（原生 HTML/CSS/JS，无需构建工具）。

## 架构

```
浏览器 (web/)
   │  POST /api/query/stream        ── SSE：progress / result / error / done
   ▼
FastAPI (server.py)
   │  直接调用 ../作业一/sql_agent
   ▼
SQLAgentGraph.astream(stream_mode="updates")
   │  每个图节点的状态增量
   ├── select_tables     → 选择相关表
   ├── generate_sql      → 生成 SQL
   ├── validate_execute  → 校验并执行（或记录错误）
   ├── rewrite_sql       → 修复 SQL（循环）
   ├── summarize         → 汇总答案
   └── finalize          → WorkflowOutput（完整结果 JSON）
   ▼
Chinook SQLite（只读）
```

选择 SSE 而不是轮询的原因：一个问题的耗时主要在一次到几次模型调用上（秒级），
按节点推送进度既能让用户知道卡在哪一步，也不需要额外的任务队列和状态存储；
`astream` 直接把 LangGraph 的节点更新转成事件，没有轮询间隔和无效请求。

## 文件说明

| 路径 | 说明 |
| --- | --- |
| `server.py` | FastAPI 服务：静态页面、健康检查、配置、普通查询、SSE 流式查询 |
| `web/index.html` | 页面结构：顶栏状态、对话区、示例问题、输入框 |
| `web/styles.css` | 样式：明暗主题、消息气泡、进度时间线、结果卡片、响应式 |
| `web/app.js` | 交互：SSE 解析、进度渲染、Markdown 精简渲染、SQL 复制、中断请求 |
| `scripts/smoke_test.py` | 离线测试：页面/静态资源/接口校验/进度事件映射，不调用模型 |
| `requirements.txt` | Web 与后端依赖 |

## 启动

```bash
cd 詹光明/week13/作业二

pip install -r requirements.txt
# 如果已经装过 作业一 的依赖，只需 pip install fastapi "uvicorn[standard]"

# 模型 Key 与数据库沿用 作业一 的配置（../作业一/.env 或环境变量）
python server.py
```

默认地址 `http://127.0.0.1:8000/`。手机/局域网访问：

```bash
SQL_AGENT_WEB_HOST=0.0.0.0 python server.py
# 然后访问 http://<电脑局域网IP>:8000/
```

> 前端本身不接触模型 Key，所有请求都走本机后端；不要把服务直接暴露到公网（无鉴权）。

## 前端功能

- **对话式输入**：Enter 发送、Shift+Enter 换行；中文输入法选词时按 Enter 不会误发送。
- **实时进度**：每个图节点完成后立即显示「✓ 选表 / 生成 SQL / 校验并执行 / 改写 / 汇总」，
  失败节点标红并显示错误码信息。
- **结果卡片**：答案支持加粗、行内代码、代码块和 Markdown 表格（后端降级输出就是表格）；
  展示表名、行数、改写次数、耗时等标签，以及 `evidence` / `caveats`。
- **可追溯**：可展开查看最终 SQL（一键复制）和完整 `WorkflowOutput` JSON。
- **可中断**：查询过程中点「停止」会中止请求，图执行随连接一起取消。
- **状态提示**：页面顶部显示后端/模型连接状态；缺少 API Key 或数据库时给出具体操作提示。
- **明暗主题**：跟随系统 `prefers-color-scheme`，移动端自适应。
- **无构建、无 CDN**：纯静态资源，离线也能打开页面。

## 接口

### GET /api/health

```json
{
  "status": "ok",
  "ready": true,
  "model": "deepseek-chat",
  "summary_model": "deepseek-chat",
  "base_url": "https://api.deepseek.com",
  "database": "chinook.db",
  "database_exists": true,
  "api_key_configured": true,
  "error": null
}
```

`status` 取值：`ok` / `missing_key` / `missing_db` / `error`。

### GET /api/config

返回前端展示用的模型名、库表清单、重试上限和示例问题。

### POST /api/query

非流式查询，便于脚本或 curl 调用：

```bash
curl -X POST http://127.0.0.1:8000/api/query \
  -H "Content-Type: application/json" \
  -d '{"question":"每个国家的客户数量，按数量降序"}'
```

```json
{
  "question": "每个国家的客户数量，按数量降序",
  "output": {
    "question": "每个国家的客户数量，按数量降序",
    "answer": "客户数量最多的国家是美国，共 13 位……",
    "sql": "SELECT Country, COUNT(*) AS CustomerCount FROM Customer GROUP BY Country ORDER BY CustomerCount DESC",
    "tables": ["Customer"],
    "row_count": 24,
    "truncated": false,
    "repair_attempts": 0,
    "evidence": ["美国：13", "加拿大：8"],
    "caveats": [],
    "error": null
  },
  "elapsed_ms": 5230
}
```

### POST /api/query/stream

请求体同上，响应为 `text/event-stream`：

```
event: progress
data: {"stage":"select_tables","label":"选择相关表","status":"done","detail":"Customer","tables":["Customer"]}

event: progress
data: {"stage":"generate_sql","label":"生成 SQL","status":"done","detail":"SELECT Country, ..."}

event: progress
data: {"stage":"validate_execute","label":"校验并执行","status":"done","detail":"返回 24 行"}

event: progress
data: {"stage":"summarize","label":"汇总答案","status":"done","detail":"已根据查询结果生成回答"}

event: result
data: {"request_id":"3f2c...","output":{...},"elapsed_ms":5230}

event: done
data: {"request_id":"3f2c...","elapsed_ms":5230}
```

错误以 `event: error` 返回（如模型鉴权失败、网络超时）；请求参数错误仍返回普通 HTTP 400/422。

## 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `SQL_AGENT_WEB_HOST` | `127.0.0.1` | 监听地址；局域网访问设为 `0.0.0.0` |
| `SQL_AGENT_WEB_PORT` | `8000` | 监听端口 |
| `SQL_AGENT_LOG_LEVEL` | `INFO` | 服务端日志级别 |

模型、数据库、重试次数等配置全部沿用 `作业一` 的环境变量（`OPENAI_API_KEY`、
`OPENAI_BASE_URL`、`SQL_AGENT_MODEL`、`SQL_AGENT_DB` 等），详见
[`../作业一/README.md`](../作业一/README.md)。

## 实现要点

- **复用而非复制**：`server.py` 把 `../作业一` 加入 `sys.path` 后导入 `sql_agent`，
  `SQLAgentGraph` 在进程启动时编译一次；数据库目录和 `.env` 都由后端包自行解析。
- **节点 → 事件**：`_progress()` 只读取每个节点的状态增量（`selection`、`sql`、
  `execution`、`validation`、`repair_attempts`），不参与业务逻辑，因此前端展示与图流程解耦。
- **结果只有一个来源**：最终数据直接取 `finalize` 节点产出的 `WorkflowOutput`，
  与 CLI 的 JSON 完全一致，避免 Web 层再拼一套字段。
- **取消与异常**：客户端断开时 `StreamingResponse` 会取消生成器，图执行随之停止；
  节点异常、模型超时都会转成 `error` 事件，不会让连接悬挂。
- **安全**：前端不能直接访问数据库或模型；后端沿用作业一的只读连接、单语句门禁、
  `EXPLAIN` 校验和执行超时。问题长度限制 2000 字符。

## 测试

```bash
python scripts/smoke_test.py
```

覆盖：首页与静态资源可访问、健康检查、配置接口、空白/超长问题被拒绝、
未配置 Key 时查询被拦截、进度事件映射正确。测试不调用模型，也不需要 API key。

## 已知限制

- 仍是**单轮查询**：页面像聊天，但每次提问独立，不携带上一轮上下文。
  后端图已支持检查点，后续可加 `thread_id` 做多轮。
- 没有用户鉴权、限流和持久化历史；仅适合本机/内网使用。
- 前端只实现了够用的 Markdown 子集（加粗、代码、表格），复杂排版会原样显示。
- 同一进程内多个查询共享模型客户端和编译后的图；高并发需要再加信号量或独立 worker。
