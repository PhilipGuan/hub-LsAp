# sql-agent-langgraph

使用 **LangGraph** 复现 `task1_copy/00-sql-agent`（原 openai-agents 实现）的 Chinook SQL Agent。

设计文档见上级目录：`../DESIGN_sql_agent_langgraph.md`。

## 架构

```
START → select_tables → route_selection → (generate_sql | select_tables↺ | finalize)
generate_sql → validate_sql → route_validate → (execute_sql | rewrite_sql | finalize)
execute_sql  → route_execute  → (summarize   | rewrite_sql | finalize)
rewrite_sql  → route_rewrite  → (validate_sql↺ | finalize)
summarize → finalize → END
```

- **编排**：`StateGraph` + 5 个条件边路由函数（`routing.py`），替代原实现的手写 for/if 状态机
- **LLM 节点**：`ChatOpenAI(...).with_structured_output(契约类)`，替代原 `Agent(output_type=...)`
- **确定性组件**（`db/`，与原实现逐字一致）：`SchemaCatalog` / `SQLValidator` / `SQLExecutor`
- **数据契约**（`models.py`）：与原实现字段一致，`WorkflowOutput` 可直接对比
- **语义保持**：修复预算 3 次（校验/执行共享）、选表反馈重试 1 次、空结果不重试、汇总失败降级 Markdown 表格、`cannot_fix` 提前终止
- **LangGraph 特性**：`attempts` 使用 `operator.add` reducer；`InMemorySaver` checkpointer 常开，支持 `--thread-id`、逐节点流式（`--stream`）与 `state_history` 回放

## 运行

```bash
cd sql_agent_langgraph
pip install -e . --no-deps        # 或直接 PYTHONPATH=src python -m sql_agent_langgraph

# 1) 必填：API key 只从环境变量读取（当前尚未配置，需补充）
export OPENAI_API_KEY=sk-...
# 2) 可选：模型与端点
export OPENAI_BASE_URL=https://api.deepseek.com   # 默认；DashScope 兼容端点亦可
export SQL_AGENT_MODEL=deepseek-flash             # 选表/生成/改写（默认 deepseek-flash）
export SQL_AGENT_SUMMARY_MODEL=deepseek-flash     # 汇总

# 单次查询（JSON 到 stdout，日志到 stderr）
python -m sql_agent_langgraph "数据库里有多少张专辑？"

# 逐节点流式 + 指定 checkpoint 线程
python -m sql_agent_langgraph --stream --thread-id demo-1 "销量最高的10首歌曲？"
```

常用环境变量（全部可选）：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `SQL_AGENT_DB` | `ref/chinook.db` | 数据库路径（项目内已自带副本） |
| `SQL_AGENT_MAX_REPAIR_ATTEMPTS` | `3` | 修复预算（校验/执行共享） |
| `SQL_AGENT_MAX_SELECTION_RETRIES` | `1` | 选表反馈重试次数 |
| `SQL_AGENT_MAX_ROWS` / `SQL_AGENT_MAX_SUMMARY_ROWS` | `200` / `50` | 执行/汇总行数上限 |
| `SQL_AGENT_QUERY_TIMEOUT` | `5.0` | 执行超时（秒） |
| `SQL_AGENT_LOG_LEVEL` | `INFO` | 日志级别 |

## 测试

```bash
python -m pytest -q          # 36 个用例：路由分支 / reducer / 确定性组件 / fake-LLM 整图
```

fake-LLM 整图测试（`tests/test_graph_fake.py`）用预置的结构化返回驱动完整图，
覆盖：正常路径、修复回边、预算耗尽、cannot_fix、选表反馈重试、澄清短路、
空结果跳过汇总、汇总降级、流式与 checkpoint 回放。

### DeepSeek 端点兼容性实测（2026-11-03）

| 问题 | 现象 | 处理 |
| --- | --- | --- |
| `with_structured_output` 默认 `response_format=json_schema` | 400: This response_format type is unavailable now | 改 `method="function_calling"`（tools 接口） |
| `deepseek-flash` 默认思考模式，不支持强制 `tool_choice` | 400: Thinking mode does not support this tool_choice | 显式 `tool_choice="auto"`，模型在提示词约束下仍调用 schema 工具 |
| 状态中的 Pydantic 契约经 checkpoint 走 pickle 兜底 | langgraph 警告未来版本将拒绝 | `_default_checkpointer()` 按 `(module, 类名)` 注册全部契约类并关闭 pickle 兜底 |

以上修复集中在 `llm.py` 与 `graph.py`，全部用例与真实 LLM 金标场景
（单表聚合 / 两表·三表 JOIN / 日期过滤 GROUP BY / 空结果 / 无关问题澄清 / `--stream`）实测通过。

> 注：原实现（`task1_copy/00-sql-agent`，openai-agents 0.23.1）在当前 DeepSeek 端点上
> 同样因 `response_format=json_schema` 返回 400，且其 schema 不注入 prompt、无法用
> `json_object` 替代——即原代码按现状已无法在该端点运行。行为对比验收因此采用
> "契约字段静态比对（同一 models.py）+ 编排语义 fake 测试对齐"的方式完成。

## 与原实现的对应关系

| 原（openai-agents） | 本项目（LangGraph） |
| --- | --- |
| `workflow.SQLAgentWorkflow._run` 手写循环 | `graph.py` 拓扑 + `routing.py` 条件边 |
| `Agent(output_type=...)` × 4 | `llm.py` 中 4 个 `with_structured_output` 链 |
| `Runner.run(agent, prompt)` | 节点内 `llm.invoke(prompt)` |
| `state.attempts.append(...)` | `attempts: Annotated[list, operator.add]` |
| `agents.trace()` | checkpointer 状态历史 + `--stream` 逐节点更新 |
| `db/`、`models.py`、提示词 | 原样复用 |
