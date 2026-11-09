# Chinook SQL Agent（LangGraph 版）

基于 **LangChain 手写状态机**的 SQL Agent 的**框架替换**：
把编排层从 `async def _run` 的显式 `for` 循环，改为 **LangGraph `StateGraph`** 的
节点 + 条件边，其余能力（结构化输出 Agent、确定性校验/执行、只读 DB 访问）保持不变。

## 工作流

```
数据库解析 → 表选择 → SQL 生成 → SQL 校验 → SQL 执行 → 结果汇总
                                  ↑            │
                                  └── SQL 改写 ←┘  （共享 3 次修复预算）
```

LangGraph 图结构（见 `src/sql_agent/graph.py`）：

```
START
  └─> table_select ──澄清──> finalize
        └─> generate_sql ──> validate
              ├─ 通过 ─────────────────> execute ──成功──> summarize ──> finalize
              └─ 失败 ──> rewrite ──> (回到) validate
                                  └─ cannot_fix / 超预算 ──> fail ──> finalize
finalize ──> END
```

| 组件 | 类型 | 框架 |
| --- | --- | --- |
| 编排 | LangGraph `StateGraph` + 条件边 | `langgraph` |
| 四个 Agent | `StructuredAgent`（`prompt \| ChatOpenAI.bind_tools`） | `langchain` |
| 校验 / 执行 / 解析 | 确定性代码（只读、`EXPLAIN`、超时、截断） | `sqlalchemy` / `sqlite3` |

## 运行

```bash
# 需要 OPENAI_API_KEY；默认走 DeepSeek 兼容端点
export OPENAI_API_KEY=<你的 DeepSeek API Key>
# 注意：本工程用 ChatOpenAI（OpenAI 协议），必须用 DeepSeek 的 OpenAI 兼容端点，
# 不能带 /anthropic 后缀——那是 Anthropic Messages 协议端点，会被请求成
# https://api.deepseek.com/anthropic/chat/completions 而返回 404。
# 下面这行通常无需设置，config.py 默认即 https://api.deepseek.com。
export OPENAI_BASE_URL=https://api.deepseek.com   # 可选

PYTHONPATH=src python3 -m sql_agent "总共有多少张专辑？"
```

- 日志输出到 stderr，最终 JSON 输出到 stdout。
- 模型覆盖：`SQL_AGENT_MODEL` / `SQL_AGENT_SUMMARY_MODEL`（默认 `deepseek-chat`）。
- `SQL_AGENT_TOOL_CHOICE` 默认 `auto`（思考模式模型只接受 `auto`）。

## 测试

```bash
pytest -q          # 无需 API key，LLM 调用被 mock
ruff check src tests
```

测试分层：`test_db_tools.py`（catalog/校验/执行）、`test_llm.py`（结构化输出装配）、
`test_graph.py`（LangGraph 图结构与路由、重试/预算/降级路径）、`test_logging.py`。
