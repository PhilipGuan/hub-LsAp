# 能力清单（FEATURES）· 本项目体现了哪些 LangGraph / LangChain 特性

这份文档只回答一类问题：**这个项目到底演示了框架的哪些能力，落在哪一行。**

配套阅读：
- [README.md](README.md) —— 怎么跑；
- [DESIGN.md](DESIGN.md) —— 为什么长这样（设计取舍）；
- `sql-agent-advanced.ipynb` —— 把三件套跑给你看的演示 notebook。

> 一句话定位：本项目对 LangGraph 的覆盖是**「少而深」**——
> 只打三根钉子（**持久化 / 人工介入 / 时间旅行**），但每根都做到
> 「子图嵌套 + 边界条件 + 离线测试钉死」的程度；LangChain 侧则完整演示了
> `ChatOpenAI → 提示模板 → 结构化输出 → @tool → create_agent` 的经典链路。

---

## 一、LangGraph 部分（本项目重点）

### 1. 显式状态图：`StateGraph` + `TypedDict` 状态

把「流程」写成图，而不是手写 `for/if`。

| 特性 | 落点 | 说明 |
|---|---|---|
| `StateGraph(State)` + `TypedDict(total=False)` | `sqlagent/workflow/graph.py`（`WorkflowState`） | 状态即字典，与原实现的 `trace` 一一对应 |
| 节点 = 纯函数 `state -> dict` | `sqlagent/workflow/graph.py`（`node_select` / `node_generate` / …） | 7 个阶段各一个节点 |
| `START` / `END` 哨兵 | `sqlagent/workflow/graph.py`（构图段） | 首尾显式连边 |
| `add_edge` 普通边 | 同上 | `select → generate → check` |
| `add_conditional_edges` 条件边 | `sqlagent/workflow/graph.py`（`route_after_check`） | 决定「执行 / 改写 / 放弃」 |
| **回环（cycle）** | `sqlagent/workflow/graph.py`（`rewrite → check`） | SQL 报错→重写→再校验的自动重试环 |
| 图可视化 | `sqlagent/workflow/graph.py`（模块 docstring） | `graph.get_graph().draw_mermaid()` 直接画拓扑 |
| `compile()` | `sqlagent/workflow/graph.py` | 拆出「未编译 / 已编译」两层 |

### 2. 状态 Reducer（`Annotated` + `operator.add`）

会话图里 `turns`（对话历史）用**追加式 reducer**——这是 LangGraph「状态怎么合并」的原生语义，手写循环做不出：

```python
turns: Annotated[list, operator.add]    # 追加式对话历史
```

落点：`sqlagent/conversation/graph.py`（`ConversationState` 定义与 `node_finalize` 写入处）。

### 3. 子图组合（Subgraph）

`add_node("sql", sql_subgraph)` 把上一版整张 7 阶段图**当一个节点挂进来**，并利用「同名字段自动透传」省掉手工搬运。

- 挂载处：`sqlagent/conversation/graph.py`；
- 状态刻意同名：`ConversationState` 复用 `WorkflowState` 的字段；
- **LangGraph 硬约束「子图不得自带 checkpointer」**：`sqlagent/workflow/graph.py`（`_build_workflow` 拆分层）；
- 父子图各有一套 `checkpoint_ns`：回放时正是靠 `StateSnapshot.config` 里的 `checkpoint_ns` 才能定位到子图那一跳（`sqlagent/time_travel.py` 的 `replay_from`）。

### 4. Checkpointer 持久化 + `thread_id`

| 机制 | 落点 |
|---|---|
| `InMemorySaver` / `SqliteSaver` 工厂 | `sqlagent/session.py`（`make_checkpointer`） |
| `{"configurable": {"thread_id": ...}}` 约定 | `sqlagent/session.py`（`thread_config`） |
| `compile(checkpointer=...)` | `sqlagent/workflow/graph.py`（`build_checkpointed_graph`） |
| 多轮记忆读自检查点（而非 Python list） | `sqlagent/conversation/bi.py`（`ConversationBI.turns`） |

对比点：老版 `ChatBI.history` 是个 `list`，进程退出即失；新版按 `thread_id` 落库。

### 5. Human-in-the-loop：`interrupt()` + `Command(resume=...)`

LangGraph **最有辨识度**的一组 API，项目用了两处：

| 用途 | 落点 |
|---|---|
| SQL 执行前人工复核闸门（approve / edit / reject） | `sqlagent/workflow/graph.py`（`node_human_review` + `route_after_review`） |
| 澄清反问 → 暂停 → 续接回环 | `sqlagent/conversation/graph.py`（`node_clarify` + `clarify → rewrite` 回环边） |
| `Command(resume=...)` 送回答复 | `sqlagent/hitl.py`（`review_loop`） |
| `__interrupt__` 载荷提取 | `sqlagent/hitl.py`（`pending_interrupt`） |
| **子图 interrupt 冒泡到父图外部** | `sqlagent/conversation/bi.py`（统一以 `status="interrupted"` 暴露） |

关键取舍：**节点内绝不 `input()`，`interrupt()` 只负责抛问题；`input()` 在图外**——
详见 [DESIGN.md](DESIGN.md) 第 3.2 节。

### 6. 时间旅行：`get_state` / `get_state_history` / `update_state`

| 机制 | 落点 |
|---|---|
| `get_state_history()` 吐检查点链 | `sqlagent/time_travel.py`（`list_hops`） |
| `StateSnapshot` 解构（`values` / `metadata` / `next` / `config`） | 同上（`step` / `source` / `checkpoint_id`） |
| 从某一跳**分叉**重跑：`update_state()` → 拿新 config → `invoke(None, cfg)` | `sqlagent/time_travel.py`（`replay_from`） |
| 定位「下一跳进入 X」的检查点 | `sqlagent/time_travel.py`（`checkpoint_before`） |
| 读当前线程状态 | `sqlagent/conversation/bi.py`（`ConversationBI.turns` 用 `get_state`） |

坑点已写进注释：`update_state()` 会返回**分叉后的新 config**，不接住就会跑回旧检查点。

### 7.「确定性图」 vs 「自主 Agent」两种驱动方式

同一个内核，两种 LangGraph 用法对照：

- **编排写在 Python 里**：`sqlagent/workflow/`（每步一次调用、拓扑固定）；
- **决策权交给模型**：`sqlagent/agents/`（`create_agent` 内部**本身就是** LangGraph 实现）。

---

## 二、LangChain 部分

| LangChain 特性 | 落点 | 用途 |
|---|---|---|
| `ChatOpenAI`（显式 `api_key` / `base_url`，不污染 `os.environ`） | `sqlagent/llm.py`（`build_raw_model`） | 接 DeepSeek 的 OpenAI 兼容端点 |
| **Runnable 协议**（统一 `.invoke()`） | 全项目 | 模型、`RunnableRetry`、结构化输出、Agent、图都是 Runnable，可互换 |
| `with_retry(stop_after_attempt=3)` | `sqlagent/llm.py`（`build_chat_model`） | 替代手写三次 try 循环 |
| `with_fallbacks([...])`（模型兜底） | `sqlagent/llm.py`（`build_chat_model` / `invoke_structured_messages`） | 主模型重试仍失败时自动切备用模型（`LLM_FALLBACK_PROVIDER` / `LLM_FALLBACK_MODEL`）；`ModelFallbackMiddleware` 的图原语等价物 |
| `with_structured_output(Pydantic)` | `sqlagent/llm.py`（`invoke_structured_messages`） | 替代 `json_mode + json.loads + 兜底` |
| Pydantic 输出契约模型 | `sqlagent/schemas.py` | `TableSelection` / `ScopeJudgement` / 4 个 Judge |
| `ChatPromptTemplate.from_messages` | `sqlagent/prompts.py` | 替代 f-string 拼接，命名变量 + 分类复用 |
| 消息类型 `SystemMessage` / `HumanMessage` / `BaseMessage` | `sqlagent/llm.py` | — |
| `@tool` 装饰器（签名 + docstring 自动生成 schema） | `sqlagent/tools.py`（`build_tools`） | `list_tables` / `parse_table` / `run_sql` |
| 工具工厂 + 闭包绑定依赖 | `sqlagent/tools.py` | 避免全局变量 |
| `create_agent`（LangChain v1） | `sqlagent/agents/sql_agent.py` | 三角色 Agent + 单 Agent 自主路线 |
| **`HumanInTheLoopMiddleware`**（agent 路线的人工闸门） | `sqlagent/agents/sql_agent.py`（`build_agent_middleware`） | `run_sql` 执行前 `interrupt()` 等放行 / 改写 / 驳回 |
| **`ModelFallbackMiddleware`**（agent 路线的模型兜底） | `sqlagent/agents/sql_agent.py`（`build_agent_middleware`） | 主模型失败时切备用模型；`create_agent` 只收真 `BaseChatModel`，`.with_fallbacks()` 喂不进去，故必须用 middleware |
| Agent 轨迹还原为多轮会话 | `sqlagent/agents/session.py`（`AgentBI.turns` / `_last_run_sql`） | 与 `ConversationBI` 同形，server 端一个会话表容纳两条路线 |
| Agent 消息态轨迹解析 | `sqlagent/agents/sql_agent.py`（`_last_ai_text`） | 从 `{"messages": [...]}` 取最后一段 AI 文本 |
| 懒导出 / 依赖隔离（PEP 562） | `sqlagent/__init__.py` | `import sqlagent` 不牵出 matplotlib / IPython |

---

## 三、一组很「进阶」的设计主张

不是 API，但比 API 更值钱：

1. **框架边界感**：`sqlagent/guard.py` 的 `is_readonly()` **刻意不套 LangChain、不写进提示词**——
   护栏必须做进**代码结构**（「Structure vs Prompt」）。
2. **契约优先**：`with_structured_output` 解析失败**返回 `None` 而不是猜**，
   把「降级成什么」的决策权留给调用方（见 `sqlagent/schemas.py` 模块说明）。
3. **行为不变量**：签名、LLM 调用次数、提示词逐字不变（见 [DESIGN.md](DESIGN.md) 第 4 节）——
   「换框架 ≠ 换行为」。
4. **可验证性**：离线单测把上面每条机制都钉死，例如
   `tests/test_conversation_graph.py`（interrupt / resume 三分支）、
   `tests/test_time_travel.py`（分叉回放）。
5. **双语与「加语言 ≠ 加行为」**：中英双语（网页默认英文）做成**附加 `lang` 参数 + 默认 `zh`**，
   文案收在单一事实源 `sqlagent/i18n.py`；只给 4 条**产出自然语言**的提示词配英文变体
   （`summarize` / `scope` / `rewrite_followup` / `summarize_history`），
   **选表 / 生成 SQL / 改写故意不配英文版**（产物是表名与 SQL）。
   它**不是** LangGraph / LangChain 的原生特性，而是把上面第 3 条「行为不变量」
   原样延伸到 i18n 上的工程化示范（既有 111 项测试一条未改，新增 39 项）。
   详见 [DESIGN.md](DESIGN.md) 第 8 节。

---

## 四、诚实标注：**没覆盖**的特性

用「覆盖度」评价时，以下能力项目里**没有**（部分在 [DESIGN.md](DESIGN.md) 第 6 节「已知限制」里也承认了）：

| 未覆盖 | 说明 |
|---|---|
| **Streaming** | 没有 `stream()` / `astream_events()`，全部同步 `invoke()` |
| **Send / map-reduce 并行** | 没有 `Send` API、没有 fan-out / fan-in 并发分支 |
| **长期记忆 `BaseStore`** | 只用了 checkpointer（线程内记忆），没用跨线程的 `store` |
| **`interrupt_before` / `interrupt_after` 静态断点** | 改用 `interrupt()` 动态方式（更好，但没演示静态那种） |
| **LangSmith / 可观测性** | 没有 tracing 接入 |
| **部分 LangChain middleware** | agent 路线（`create_agent`）已挂 `HumanInTheLoopMiddleware` + `ModelFallbackMiddleware`（见上表）；**手写图路线**不用 middleware，改以图原语等价实现（`.with_fallbacks()` 兜底、摘要节点近似 `SummarizationMiddleware`）。`tool_error` / `tool_retry` **刻意不加**：`run_readonly_sql()` 从不抛异常，把失败当字符串回给模型自纠（「错误即观察」），框架层再拦一次是冗余 |
| **`langgraph.prebuilt`** | 走的是 `langchain.agents.create_agent`，没用 `create_react_agent` / `ToolNode` |

---

## 五、速查表：一个特性能不能「只靠手写 for/if 做出来」

| 能力 | 手写能否替代 | 本项目结论 |
|---|---|---|
| 7 阶段流水线 | 能（就是 `for` + `if`） | 图的写法**只是更清晰**，不算 LangGraph 独有 |
| 澄清反问→等待→续接 | **很难** | 需要「暂停 + 持久化 + 续接」，图的 checkpointer 才顺 |
| 执行前人工复核 | **很难** | 同上；`interrupt()` 是原生语义 |
| 失败轨迹与回放 | **很难** | `get_state_history()` + `update_state()` 是原生能力 |

> 判据来自 [DESIGN.md](DESIGN.md) 第 1 节：
> **这三件事，用「手写 for/if」都做不出来（或做得很别扭），
> 只有把状态交给 LangGraph 的 checkpointer 才顺理成章。**
> 这正是「LangGraph 进阶」与「LangGraph 复刻」的分水岭。
