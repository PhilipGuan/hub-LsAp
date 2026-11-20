# 搬迁对照表（MIGRATION）

把 [`04_SQL-Code-Agent-Demo/sql-agent-workflow.ipynb`](../04_SQL-Code-Agent-Demo/sql-agent-workflow.ipynb)
（11 节 notebook，裸 OpenAI SDK）**搬进本项目的模块**。

**原则**：函的搬函数、抄的抄正文。凡涉及「行为」的东西（提示词正文、重试次数、`temperature`、
失败语义、判分阈值）**一律不改**；只改「用什么部件实现它」。

---

## 1. 逐节对照

| 原 notebook 节 | 搬到本项目的哪个文件 | 改了什么 |
|---|---|---|
| `## 0. 环境准备` | [sqlagent/config.py](sqlagent/config.py) | `find_up` / `_redact` / `.env` 加载逻辑照搬；**去掉**「把 DeepSeek Key 伪装成 `OPENAI_API_KEY` 写进 `os.environ`」——`ChatOpenAI` 支持显式传参 |
| `## 1. 数据库解析（DBParser）` | [sqlagent/db.py](sqlagent/db.py) | 逐字移植；仅按 SQLAlchemy 2.x 规范修两处写法（`filter(col == None)`→`where(col.is_(None))`、用 `with engine.connect()` 包住元数据抓取），**对外行为一致** |
| `## 2. 只读安全守卫` | [sqlagent/guard.py](sqlagent/guard.py) | `is_readonly()` **逐字移植，一字未改**。这是纯 Python 护栏，**刻意不 LangChain 化** |
| `## 3. LLM 封装（DeepSeek · Chat Completions）` | [sqlagent/llm.py](sqlagent/llm.py) + [sqlagent/prompts.py](sqlagent/prompts.py) | `chat()` → `ChatOpenAI` + `ChatPromptTemplate`；手写 3 次重试 → `with_retry`；`json.loads` 兜底 → `with_structured_output`（契约在 [schemas.py](sqlagent/schemas.py)） |
| `## 4. 流水线各阶段函数` | [sqlagent/workflow/graph.py](sqlagent/workflow/graph.py) + [sqlagent/tools.py](sqlagent/tools.py) | `table_catalog` / `schema_context` 搬进 `tools.py`；`select_tables` / `generate_sql` / `rewrite_sql` / `summarize` 搬进 `graph.py`，提示词逐字保留 |
| `## 5. 编排：7 阶段串接 + 校验/改写闭环` | [sqlagent/workflow/graph.py](sqlagent/workflow/graph.py) | 手写 `for`/`if` → LangGraph `StateGraph`（节点 + 条件边 + 回环）；`MAX_REWRITE`、`FAIL_ANSWER` 语义不变 |
| `## 6. Demo：跑几个示例问题` | `sql-agent-langchain.ipynb` + [sqlagent/cli.py](sqlagent/cli.py) | Demo 变成「可执行的 notebook」+「命令行」两种跑法 |
| `## 7. 问题模拟生成器（QuestionSimulator）` | [sqlagent/evalset.py](sqlagent/evalset.py) | 三个造题函数 + `values_match` 照搬 |
| `## 8. 小型评测` | [sqlagent/evalset.py](sqlagent/evalset.py) | `build_eval_set()` 保留为 `simulate_questions(..., style="count")` 的特化调用 |
| `## 9. 范围判定与用户对接（ChatBI）` | [sqlagent/chatbi.py](sqlagent/chatbi.py) | `scope_check` / `rewrite_followup` / `render_result` / `ChatBI` 照搬；`scope_check` 的 JSON 解析换成 `ScopeJudgement`（Pydantic），**结构兜底语义不变** |
| `## 10. 逐层裁判验证` | [sqlagent/judge/](sqlagent/judge/) + [sqlagent/evalset.py](sqlagent/evalset.py) | `GOLDEN_SET` 冻结 + 12 题照搬；确定性判分进 `deterministic.py`，LLM 判分进 `llm.py`，编排进 `__init__.py` |
| `## 11. 结论与已知限制` | [README.md](README.md#8-已知限制) | 整改写进 README 的「已知限制」 |

---

## 2. 函数级对照（原实现 → 本实现）

| 原函数 | 本实现 | 说明 |
|---|---|---|
| `find_up` / `_redact` | `config.find_up` / `config.redact` | 照搬 |
| `DBParser` | `db.DBParser` | 逐字移植 |
| `is_readonly` | `guard.is_readonly` | 逐字移植 |
| `chat(system, user, json_mode=)` | `llm.invoke_text` / `llm.invoke_structured(_messages)` | 拆成「纯文本」与「结构化」两条，语义不变 |
| `strip_code_fence` | `llm.strip_code_fence` | 照搬 |
| `table_catalog` / `schema_context` | `tools.table_catalog` / `tools.schema_context` | 照搬 |
| —— | `tools.run_readonly_sql` | **新增**：把「守卫 → 试跑 → 执行」收成一个具名入口；原实现散在 cell 里 |
| —— | `tools.build_tools` | **新增**：LangChain `@tool`（`list_tables` / `parse_table` / `run_sql`），仅供 `create_agent` 用 |
| `select_tables` | `workflow.graph.select_tables` | 提示词逐字保留，改用 `TableSelection` 结构化输出 |
| `generate_sql` | `workflow.graph.generate_sql` | 照搬 |
| `rewrite_sql` | `workflow.graph.rewrite_sql` | 照搬 |
| `summarize` | `workflow.graph.summarize` | 照搬 |
| `run_workflow` | `workflow.graph.run_workflow` | 返回字段**完全一致**；内部从手写循环改为 `StateGraph.invoke` |
| `scope_check` | `chatbi.scope_check` | 结构化输出替换 `json.loads`，结构兜底保留 |
| `rewrite_followup` | `chatbi.rewrite_followup` | 照搬 |
| `render_result` | `chatbi.render_result` | 照搬 |
| `ChatBI` | `chatbi.ChatBI` | 照搬（`ask` 返回的 trace 字段一致） |
| `simulate_*` / `values_match` / `build_eval_set` | `evalset.*` | 照搬 |
| `freeze_golden` / `judge_*` / `JudgeAgent` / `run_and_judge` | `judge/*` + `evalset.freeze_golden` | 拆分文件，逻辑照搬 |

---

## 3. **新增**的东西（原实现没有）

| 新增 | 文件 | 为什么加 |
|---|---|---|
| `create_agent` 三角色 Agent + 单 Agent 自主路线 | [agents/sql_agent.py](sqlagent/agents/sql_agent.py) | 复刻参考 `sql-agent.ipynb` 的角色划分，并补齐原参考里的两个空壳；展示「自主编排」与「确定性编排」的对照 |
| `@tool` 工具集 | [tools.py](sqlagent/tools.py) | `create_agent` 的燃料 |
| `build_raw_model()` | [llm.py](sqlagent/llm.py) | 解决 `with_retry()` 丢模型级能力的关键 bug（见 README 第 4 节） |
| Pydantic 契约 | [schemas.py](sqlagent/schemas.py) | 把「靠约定」的 JSON 变成「靠校验」的对象 |
| 命令行入口 | [cli.py](sqlagent/cli.py) | 脱离 notebook 也能跑，`--route workflow\|agent` 一键切换对照 |
| 离线单测（52 项，零网络） | [tests/](tests/) | 把「行为不变量」钉死：换框架不能改行为 |

---

## 4. 三个「故意没改」（行为不变量）

1. **提示词正文**：全部提示词在 [prompts.py](sqlagent/prompts.py) 里**逐字保留**原文，
   只把「塞进 `messages` 的字典」改成「`ChatPromptTemplate` 格式化」。
2. **调用硬参数**：`temperature=0.0`、重试 3 次、`MAX_REWRITE=3`、
   失败文案 `FAIL_ANSWER`——全部与原文一致。
3. **失败即放弃**：校验连续失败达上限时，返回固定的「已放弃（不编造结果）」文案，
   绝不退化成「猜一个答案」——这条语义在 `run_workflow` 与 `SQLAgent.run` 里都保留。

> 这三条由 [tests/](tests/) 里的断言钉住。**换框架 ≠ 换行为**，这是本次重写的验收标准。
