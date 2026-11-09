# LangGraph SQL Agent

用 **LangGraph** 复现之前 SQL Agent 的四阶段工作流，并提供一个网页聊天前端：

```text
用户自然语言问题
    ↓
表选择 select_tables
    ↓
SQL 生成 generate_sql
    ↓
SQL 执行 execute_sql
    ├─ 成功 → 答案汇总 summarize_answer → END
    └─ 失败 → SQL 修复 repair_sql → 再次执行
```

## 功能

- LangGraph `StateGraph` 显式编排 SQL Agent
- 多表选择，并把主键、外键和样例数据交给模型
- 使用 LangChain `ChatOpenAI` 调用 OpenAI 兼容接口
- Pydantic + `with_structured_output` 约束选表和 SQL 输出
- SQL 只读安全校验，只允许 `SELECT` / `WITH`
- 自动限制查询结果行数
- SQL 执行失败后最多自动修复 2 次
- FastAPI 后端接口
- SSE 流式展示 Agent 节点进度
- 网页聊天界面展示最终答案、SQL、查询结果表格
- `thread_id + InMemorySaver` 保持同一页面会话的短期记忆

## 目录结构

```text
sql_agent_langgraph/
├── app.py                    # FastAPI 入口和 SSE 接口
├── sql_agent/
│   ├── config.py             # 环境变量配置
│   ├── database.py           # 数据库结构读取和只读 SQL 执行
│   ├── models.py             # Pydantic 结构化输出模型
│   ├── prompts.py            # 四个阶段的提示词
│   └── graph.py              # LangGraph 状态、节点和条件边
├── static/
│   ├── index.html            # 聊天页面
│   ├── styles.css            # 页面样式
│   └── app.js                # SSE 解析和结果渲染
├── data/chinook.db           # Chinook SQLite 数据库
├── tests/                    # 数据库和 API 基础测试
├── requirements.txt
├── .env.example
└── run.ps1
```

## 1. 安装依赖

在项目目录打开 PowerShell：

```powershell
cd "C:\Users\江海啸\Desktop\江海啸\c语言笔记\python_study\llm笔记\llm作业\llm第十三周作业\sql_agent_langgraph"

python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

如果 PowerShell 不允许执行激活脚本，可以临时运行：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

## 2. 配置模型密钥

```powershell
Copy-Item .env.example .env
notepad .env
```

最少需要配置：

```env
LLM_API_KEY=你的模型密钥
LLM_MODEL=qwen-flash
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
```

代码没有硬编码任何 API Key。任何支持 OpenAI 兼容接口的模型都可以通过修改这三个变量接入。

## 3. 启动

```powershell
python -m uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

或：

```powershell
.\run.ps1
```

浏览器打开：

```text
http://127.0.0.1:8000
```

接口文档：

```text
http://127.0.0.1:8000/docs
```

## 4. 测试

```powershell
python -m pytest -q
```

测试不调用大模型，只验证数据库读取、只读策略、页面和基础 API。

## 5. API

### 健康检查

```http
GET /api/health
```

### 普通 JSON 问答

```http
POST /api/chat
Content-Type: application/json

{
  "message": "哪个国家的客户消费总额最高？",
  "thread_id": "user-001"
}
```

返回值包含：

```json
{
  "answer": "自然语言答案",
  "sql": "最终执行的 SQL",
  "selected_tables": ["customers", "invoices"],
  "columns": ["Country", "TotalRevenue"],
  "rows": [],
  "sql_error": "",
  "retry_count": 0
}
```

### SSE 流式问答

```http
POST /api/chat/stream
```

前端使用该接口展示节点进度和最终结果。

## 6. LangGraph 核心代码位置

- State 定义：`sql_agent/graph.py` 中的 `SQLAgentState`
- 表选择节点：`select_tables_node`
- SQL 生成节点：`generate_sql_node`
- SQL 执行节点：`execute_sql_node`
- SQL 修复节点：`repair_sql_node`
- 答案总结节点：`summarize_answer_node`
- 条件重试：`route_after_execute`
- 图构建：`build_graph`

## 7. 知识点

- SQLite、SQLAlchemy、表结构 introspection、外键关系
- LangChain 模型调用、消息体系、结构化输出
- Prompt 工程、Text-to-SQL
- LangGraph `StateGraph`、Node、Edge、Conditional Edge、循环重试
- Checkpointer 和多轮会话 `thread_id`
- SQL 安全白名单、单语句限制、结果行数限制
- FastAPI、Pydantic、SSE
- HTML、CSS、JavaScript、Fetch Streams、DOM 渲染

## 8. 注意

`InMemorySaver` 只把对话状态保存在当前 Python 进程内，服务重启后会清空。作业演示足够；生产环境应换成持久化 Checkpointer，并加入登录鉴权、SQL 超时和更严格的数据库账号权限。


