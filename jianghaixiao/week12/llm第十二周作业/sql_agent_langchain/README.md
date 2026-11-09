# 作业2：用 LangChain 复刻 SQL Agent

> 基于第十一周作业的 `sql_agent`（OpenAI 兼容 client / OpenAI Agents 思路），
> 用 **LangChain** 重写一版，并与原版对比。
> 本目录先提供**架构骨架**：数据模型和提示词完整，工具/链/编排留 TODO。

## 一、两种实现

| 方案 | 文件 | 特点 |
|---|---|---|
| **A. Agent 版**（主交） | `agent_langchain.py` | `create_agent` + `@tool`，模型自主决定调哪个工具 |
| **B. Pipeline 版**（对比） | `pipeline_langchain.py` | 固定 7 步，用 LCEL 链，准确率更高、可调试 |

## 二、目录结构

```
sql_agent_langchain/
├── chinook.db              # 数据库
├── config.py               # 模型/数据库/重试配置
├── db_parser.py            # 复用第十一周：数据库解析
├── sql_validator.py        # 复用：静态校验 + EXPLAIN
├── executor.py             # 复用：只读 + 限行执行
├── schemas.py              # Pydantic：TableSelection / SQLPlan / FinalAnswer
├── prompts.py              # 所有提示词
├── tools.py                # 3 个 @tool（骨架）
├── chains.py               # 4 条 LCEL 链
├── agent_langchain.py      # 方案 A：create_agent
├── pipeline_langchain.py   # 方案 B：7 步 pipeline
├── demo.py                 # 演示
├── evaluation.py           # 评测骨架
├── requirements.txt
└── README.md
```

## 三、OpenAI Agents → LangChain 对应关系

| OpenAI Agents SDK | LangChain |
|---|---|
| `Agent(name=..., instructions=..., model=..., tools=[...])` | `create_agent(model=..., system_prompt=..., tools=[...])` |
| `Runner.run(agent, input)` | `agent.invoke({"messages": [...]})` |
| `result.final_output` | `result["messages"][-1].content` |
| `@function_tool` | `@tool` |
| `output_type=Pydantic` | `response_format=ToolStrategy(Pydantic)` |
| `Runner.run_streamed` | `agent.stream(..., stream_mode="messages")` |
| `handoffs=[...]` | 子 Agent 包成 tool / LangGraph 路由 |
| `session=SQLiteSession` | `checkpointer=InMemorySaver()` |

## 四、7 个步骤怎么映射

| 步骤 | 原实现 | LangChain 版 |
|---|---|---|
| 1. 数据库解析 | `db_parser.py` | **原样复用** |
| 2. Table 选择 | `select_tables()` | `chains["select"]`（`with_structured_output`）或 Agent 的 `list_tables` 工具 |
| 3. SQL 生成 | `generate_sql()` | `chains["generate"]` 或 Agent 自主生成 |
| 4. SQL 校验 | `sql_validator.py` | **原样复用**（放进 `run_sql` 工具） |
| 5. SQL 改写 | `rewrite_sql()` | `chains["rewrite"]` 或 Agent 自己重试 |
| 6. SQL 执行 | `executor.py` | **原样复用**（放进 `run_sql` 工具） |
| 7. 结果汇总 | `summarize()` | `chains["summary"]` 或 Agent 最终回答 |

## 五、运行方式

```powershell
cd "C:\Users\江海啸\Desktop\江海啸\c语言笔记\python_study\llm作业\llm第十二周作业\sql_agent_langchain"

# 1) 只看数据库结构（不需要 Key）
C:\anacond3\envs\py312\python.exe demo.py --schema

# 2) 运行 Agent 版（需填 Key + 实现 TODO）
C:\anacond3\envs\py312\python.exe demo.py

# 3) 运行 Pipeline 版
C:\anacond3\envs\py312\python.exe demo.py --pipeline

# 4) 评测
C:\anacond3\envs\py312\python.exe evaluation.py
```

## 六、TODO 清单

- [ ] `tools.py`：实现 `list_tables` / `get_table_schema` / `run_sql`
- [ ] `agent_langchain.py`：确认 `create_agent` 与 `ToolStrategy` 在 qwen 上可用
- [ ] `pipeline_langchain.py`：补全链调用的参数
- [ ] `demo.py`：跑通 5 个问题
- [ ] `evaluation.py`：对比两版准确率/耗时
- [ ] README：补上对比数据

## 七、常见坑

| # | 坑 | 解决 |
|---|---|---|
| 1 | 用了旧版 `AgentExecutor` | 课程用新 API：`from langchain.agents import create_agent` |
| 2 | qwen 不支持 provider-native 结构化输出 | 用 `ToolStrategy(Model)` |
| 3 | 工具返回 dict 报序列化错误 | `return json.dumps(...)` |
| 4 | Agent 无限调工具 | `config={"recursion_limit": 10}` |
| 5 | 模型不调工具 | system prompt 写清"必须先调 list_tables" |
| 6 | SQL 不安全 | 复用 `static_check` + `PRAGMA query_only` |
| 7 | 两版结果不一致 | 正常现象，README 里说明差异即可 |