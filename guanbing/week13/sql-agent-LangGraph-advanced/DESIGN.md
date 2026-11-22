# 设计取舍（DESIGN）· LangGraph 进阶版

这份文档只回答一类问题：**为什么长这样。**

前置背景：上一版（`07-sql-agent-langchain`）已经用 LangGraph `StateGraph`
把 7 阶段流水线**画成了一张图**——但那张图是**无状态、跑完即散**的：没有 checkpointer、
没有人工介入、轨迹靠手写 `trace` 字典。本版（`sql-agent-LangGraph-advanced`）在**不改动任何既有行为**
的前提下，把 LangGraph 真正有别于「手写 for/if」的三项能力补上。

---

## 1. 核心主张：这次进阶补的三个「缺口」，都是上一版 README 自己写下的

上一版 README 的「已知限制」里，白纸黑字承认了三件事。本版就是把它们逐条补上——
**不是新加功能，而是把自陈的短板变成能力**：

| 上一版 README 自述的缺口 | 本版补上的 LangGraph 能力 | 落在哪 |
|---|---|---|
| 「澄清反问为**单轮**：`ambiguous` 分支给出澄清语后，需用户下一条消息继续；未做反问→等待→续接的状态机」 | **持久化 Checkpointer** + `interrupt()`/`Command(resume=...)`：图可**暂停等用户回答**再续接 | [conversation/graph.py](sqlagent/conversation/graph.py) |
| 「生成 SQL **无人在环**：模型出什么就执行什么」 | **人工介入** `interrupt()`：SQL 生成后、执行前插入复核闸门，可放行/改写/驳回 | [workflow/graph.py](sqlagent/workflow/graph.py) + [hitl.py](sqlagent/hitl.py) |
| 「失败定位靠**手写 trace**」 | **时间旅行** `get_state_history()`：检查点链原生吐出一整条轨迹，还能从任意一跳重跑 | [time_travel.py](sqlagent/time_travel.py) |

> 判据很简单：**这三件事，用「手写 for/if」都做不出来（或做得很别扭），只有把状态交给
> LangGraph 的 checkpointer 才顺理成章。** 这正是「LangGraph 进阶」与「LangGraph 复刻」的分水岭。

---

## 2. 架构：把「一整轮对话」也变成一张图

上一版里，「三道闸门 + 多轮对话」是 `ChatBI` 类里**纯 Python 的编排**（`if/elif`），
只有进到 `in` 分支才交给 LangGraph 流水线。本版把这层也纳入图——因为**要暂停、要持久化，
状态就必须先在图里**。

### 2.1 拓扑（甲方案：会话图 = 父图 + SQL 子图）

```
                     ┌──────────────────── 父图（conversation）────────────────────┐
START → summarize → rewrite ──→ scope ──in──→ sql(子图) ────→ finalize → END
                      ▲           │  ├─out──────────────────→ reject ─→ finalize → END
                      │           │  └─ambiguous→ clarify ─┐
                      └───────────┴────────────────────────┘（澄清续接回环）
```

- `summarize` ← [S] 历史滚动摘要（`summarize_history`）：会话变长后把**较早的对话压成一段背景**，
  取代「只看最近 5 轮」的硬截断；短对话直接跳过（**零额外 LLM 调用**，见 §4 不变量）；
- `rewrite`  ← [A] 追问改写（`rewrite_followup`），并**重置本轮字段**，防止上一轮结果串味；
- `scope`    ← [B] 范围判定（`scope_check`），结构性兜底（声称 `in` 却没落到真实表 → 降级 `ambiguous`）；
- `sql`      ← **SQL 子图**（上一版的 7 阶段图），以 `add_node("sql", subgraph)` 挂载；
- `clarify`  ← `interrupt()` 暂停，**等用户回答**后用 `Command(resume=...)` 续接回 `rewrite`；
- `reject`   ← 越界问题直接谢绝，**不生成任何 SQL**；
- `finalize` ← 把本轮 `(问题, 回答)` 追加进 `turns`，供下一轮追问改写使用。

### 2.2 为什么是「父图 + 子图」而不是把 7 阶段摊平进一张图

| 方案 | 取舍 |
|---|---|
| **摊平成一张大图** | 7 阶段与闸门全在一个命名空间，节点名冲突风险高；最难的是——**同一张图既想独立跑、又想被嵌进去**，会互相牵绊 |
| **父图 + 子图（本版）** ✅ | SQL 子图**保持独立可跑**（`build_graph()` 依旧能单独 `invoke`，旧测试原样通过）；父图通过 `add_node` 挂载它，**状态同名字段自动透传**，无需手工搬运 |

**关键约束（LangGraph 硬性要求）**：**子图不得自带 checkpointer**——checkpointer 由父图统一持有。
所以本版把原来的 `build_graph()` 拆成两个层次：

- `_build_workflow(parser, *, human_review, verbose) -> StateGraph` —— **返回未编译的图**（构图逻辑本体）；
- `build_graph(...)` —— 编译**无 checkpointer** 版（可独立跑，也可当子图）；
- `build_checkpointed_graph(..., checkpointer=...)` —— 编译**带 checkpointer** 版（单图形态下用）。

会话图内部只调用 `build_graph()`（无 checkpointer 版）来拿子图，checkpointer 留给父图。

---

## 3. 三件套逐一设计

### 3.1 持久化：Checkpointer + `thread_id`

**问题**：上一版的多轮记忆是 `ChatBI` 里的一个 Python `list`，进程一退就没了；
被 `interrupt()` 暂停的图更是无处安放。

**做法**：给图接上 checkpointer，所有中间状态按 `thread_id` 落库。

- 工厂 [session.py](sqlagent/session.py) 的 `make_checkpointer(kind, path)`：
  - `kind="memory"` → `InMemorySaver`（**默认**，零依赖，同进程内可恢复）；
  - `kind="sqlite"` → `SqliteSaver`（落盘文件，**跨进程重启也能恢复**）。
- `thread_config(thread_id)` 把 `thread_id` 包成 LangGraph 约定的
  `{"configurable": {"thread_id": ...}}`。

**为什么默认 InMemorySaver、把 sqlite 做成可选项**：入门与单测不该被「装可选依赖 + 管文件句柄」拖累；
但**真实的多轮 / 断点续跑必须落盘**，所以 sqlite 路径完整实现并测试（`pip install langgraph-checkpoint-sqlite`）。
一句话：**默认跑得动，需要时才落盘。**

### 3.2 人工介入：`interrupt()` + `Command(resume=...)`

**问题**：模型生成 SQL 后**直接执行**，中间没有任何人把关。

**做法**：`human_review=True` 时，在 `check` 通过之后、`execute` 之前**插入 `human_review` 节点**，
节点内部调用 LangGraph 原生 `interrupt({...})`：

```
check(通过) → human_review ─┬─ approve → execute
                            ├─ edit    → check（人工改写的 SQL 必须重新过守卫与试跑）
                            └─ reject  → fail
```

- `approve` 放行执行；
- `edit` 用人工给的 SQL **回到 `check` 重新校验**（不跳守卫）；
- `reject` 直接 `fail`，可附 `note` 作为 `error`。

**`interrupt()` 只负责暂停**——它把载荷抛出图外，等外部用 `Command(resume=值)` 送回来才继续。
那个「外部」就是 [hitl.py](sqlagent/hitl.py) 的 `review_loop()`：把
「暂停 → 问人 `reviewer(payload)` → 带答复续跑」循环封装成一个小驱动器，CLI / notebook / 测试共用。

**冒泡**：子图里的 `interrupt()` 会**一路冒泡到父图外部**——所以 `ConversationBI.ask()` 能同时感知
「SQL 复核」与「澄清反问」两种暂停，统一用 `status="interrupted"` 暴露。

### 3.3 时间旅行：`get_state_history()` + 分叉重跑

**问题**：失败定位靠手写 `trace` 字典。想在「同一处境」下换提示词/换模型做 A/B 复盘，**无迹可寻**。

**做法**：接上 checkpointer 后，LangGraph 原生提供 `graph.get_state_history(config)`，
吐出一整条**检查点链**（每一跳的状态快照）。[time_travel.py](sqlagent/time_travel.py) 在此之上封装：

- `list_hops(graph, config)` —— 按时间倒序列出每一跳摘要（`checkpoint_id / step / source / next` + 关键字段）；
- `format_hops(hops)` —— 渲染成可读文本；
- `checkpoint_before(graph, config, next_node)` —— 找「下一跳将进入 `next_node`」的那个检查点；
- `replay_from(graph, config, checkpoint_id, updates=None)` —— **从某一跳回放**：
  先（可选）用 `update_state()` 改点状态**分叉**出新检查点，再 `invoke(None, cfg)` 继续跑到结束。

> 术语：**一跳（hop）= 一个检查点 `StateSnapshot`**。回放的实现细节——`update_state()` 会返回
> **分叉后的新 config**，必须接收它，否则后续 `invoke` 仍指向旧检查点（这是个易错点，代码里有注释）。

---

## 4. 契约与行为不变量（硬约束）

本次进阶**必须**满足以下不变量，否则就是「改坏了」：

1. **`run_workflow(question, parser, verbose=True)` 的签名与返回字段逐字不变** ——
   它仍走 `build_graph()`（无 checkpointer、`human_review=False`），LLM 调用次数、节点拓扑、
   「失败即放弃、不编造」的语义全部与上一版一致。
2. **默认路径 LLM 调用次数不变** —— `human_review=False` 时，`human_review` 节点**根本不加入图**，
   不产生任何额外调用。
3. **既有 52 项测试原样通过** —— 其中 `test_graph_topology_has_rewrite_cycle` 用的是
   `{"select", ...} <= set(nodes)` 的**子集判断**，所以新增节点是安全的（不会误判）。
4. **`from sqlagent.chatbi import scope_check` 等旧导入路径继续可用** ——
   `scope_check / rewrite_followup / OUT_OF_SCOPE_TEMPLATE` 被拆到 [gates.py](sqlagent/gates.py)
   后，由 `chatbi.py` **re-export**，旧路径行为不变（拆分只是为了不把 `matplotlib/IPython` 拖进会话图）。

---

## 5. 目录结构与新增文件

新增/改动集中在两处（其余为 07 基线原样复制）：

```
sqlagent/
├── session.py                ← 新增：checkpointer 工厂 + thread_id/config 助手
├── hitl.py                   ← 新增：人工介入驱动器（pending_interrupt / review_loop）
├── time_travel.py            ← 新增：时间旅行（list_hops / format_hops / replay_from）
├── gates.py                  ← 新增：从 chatbi 拆出的 [A]/[B] 两段纯逻辑（供会话图复用）
├── i18n.py                   ← 新增：中英双语文案的单一事实源（`_TEXT` / `text` / `normalise`）
├── conversation/
│   ├── graph.py              ← 新增：会话图（父图）= 三道闸门 + SQL 子图
│   ├── bi.py                 ← 新增：ConversationBI（持久化 + 可中断的会话门面）
│   └── __init__.py
├── workflow/graph.py         ← 改动：拆出 _build_workflow / build_graph / build_checkpointed_graph，
│                                新增 human_review 节点与 route_after_review
├── chatbi.py                 ← 改动：改为 re-export gates（旧导入兼容）
├── cli.py                    ← 改动：新增 --thread-id / --review / --trajectory / --replay
└── __init__.py               ← 改动：懒导出补上三件套的所有新名字
```

依赖方向仍守一条硬规则：**箭头只能从外往里指**。
`conversation/` 依赖 `workflow/`（子图）与 `gates/`（闸门）；`workflow/`、`gates/` 不反向依赖 `conversation/`；
`session/` `hitl/` `time_travel/` 是**与业务解耦的工具层**，任何图都能用。

---

## 6. 已知限制

- **`SqliteSaver` 是可选依赖**：未安装时 `make_checkpointer("sqlite")` 抛可读的 `RuntimeError`，
  提示 `pip install langgraph-checkpoint-sqlite`；默认 `memory` 不受影响。
- **`interrupt()` 必须配 checkpointer**：图若没有 checkpointer，`clarify` 与 `human_review` 无法暂停。
  `build_conversation_graph(checkpointer=None)` 仅为「不需要暂停」的极端场景保留。
- **时间旅行回放的是「状态」而非「外部副作用」**：SQL 执行本身是只读查询，重放安全；
  若未来接入有副作用的工具，回放需自行评估幂等性。
- **`review_loop` 有 `max_rounds` 防呆上界**：人工反复 `edit` 时不至于死循环。
- **checkpointer 落盘内容含对话与 SQL**：生产环境需注意落盘目录的权限与清理策略。
- **切换语言 = 新建会话**：图 / Agent 的提示词在**建会话时**就定下了，只换 UI 文案无法让
  「回答与分析」跟着换语言，所以前端一按那个蓝色按钮就自动 `新建会话`（旧会话在状态库里
  保留到 7 天过期，只是界面不再指向它）。这是取舍，不是缺陷——详见 §8。

---

## 7. 与上一版的关系（一句话）

> **上一版证明「换框架 ≠ 换行为」；本版证明「换框架 ≠ 白换」**——
> 提示词、重试次数、失败语义仍然逐字保留（第 4 节不变量），
> 但多出了三件**只有 LangGraph 才给得利落**的能力：暂停、记忆、回放。

---

## 8. 中英双语（i18n）

> 这一节记录**文档化阶段**补上的一项工程化能力。它**不属于**三件套（那三件是 LangGraph 的能力），
> 价值恰恰在于：**在不动 §4 任何一条不变量的前提下，把整条链的语言换掉**。

### 8.1 产品面

- 网页**默认英文**：标题、按钮、示例、复核卡片、错误话术，以及模型的**回答与分析**，默认都是英文；
- `[新建会话]` 右侧有一个**蓝色**按钮，文案与**当前界面语言相反**：英文态显示「中文」，中文态显示 `ENG`；
- 点一下整页翻转（含 LLM 产出的回答与结论），并把选择写进 `localStorage` —— 下次打开沿用，
  **首次访问仍是英文**。

### 8.2 为什么默认 `zh` 的核心库依然满足 §4 四条不变量

关键做法只有一句：**双语是「附加参数 + 默认值」，不是「改默认行为」**。
核心库所有新增语言入参都是 `lang: str = "zh"`：

| §4 不变量 | 双语如何不破它 |
|---|---|
| #1 `run_workflow` 签名/字段/调用次数不变 | 原函数**签名一字不动**，内部转调新拆出的 `_run_workflow(question, parser, *, verbose=True, lang="zh")`；`lang` 只决定**取哪一套文案** |
| #2 默认路径零额外调用 | `lang` 不新增任何 LLM 调用——只是换一个提示词**常量**（换文案，不换次数） |
| #3 既有测试原样通过 | 既有测试全部用**位置参数**构造门面、不传 `lang`，于是落到默认 `zh`，行为逐字不变 |
| #4 旧导入路径可用 | `OUT_OF_SCOPE_TEMPLATE` / `FAIL_ANSWER` / `CLARIFY_LIMIT_ANSWER` 的**名字与中文内容逐字保留**，值改为从 `i18n` 取 |

### 8.3 单一事实源与「刻意不全翻」

- **文案单一事实源**：[i18n.py](sqlagent/i18n.py) 的 `_TEXT`（13 条，每条中英齐全，由模块级
  `assert` 钉死）；`text(name, lang)` 取文案，`normalise(lang)` 容忍大小写与 `-`/`_` 区域后缀
  （`en-US` / `en_US` → `en`，`zh-Hans` → `zh`，认不出的一律回落 `zh`）。
- **翻译边界（只翻「产出自然语言」的提示词）**：只给 4 条提示词配了英文变体——
  `summarize` / `scope` / `rewrite_followup` / `summarize_history`；
  **「选表 / 生成 SQL / 改写」故意不配英文版**——它们的产物是**表名与 SQL**，翻译零收益。
  选择器 `*_prompt(lang)` 在 `zh` 下返回的是**原对象本身**（`is` 相等），所以默认路径连一个字节都没变。

### 8.4 语言是「会话属性」，不是「请求属性」

提示词在**建会话时**就被选定并编译进图 / Agent，因此：

```
前端 LANG（默认 en，localStorage 记忆）
   └─► POST /api/session {lang}  →  store.sessions.lang（落库）
                                      └─► _build_bot(thread_id, route, human_review, lang)
                                             └─► ConversationBI / AgentBI(lang=...)
                                                    └─► trace.answer 用哪套语言
```

- `X-Lang` 请求头只在**「还不知道是哪个会话」的失败**里兜底：口令错误 / 500 / 404
  （那几处拿不到 `sessions.lang`，用请求头决定话术语言）；
- 换句话说：**成功路径的语言来自落库的会话元数据，失败路径的语言来自请求头**；
- 也因此，**切换语言 = 新建会话**（见 §6 已知限制）：只换 UI 文案不会让旧会话的回答换语言。

### 8.5 怎么验证

```bash
pytest tests/test_i18n.py -q      # 39 项：文案层 / 提示词选择器 / 闸门话术 / 语言贯通 / 落库迁移 / CLI / API
pytest                            # 150 passed（既有 111 项一条未改）
```

真模型实测（`deepseek`）中英各一轮：英文会话 `answer = There are 3,503 tracks in the database.`，
中文会话 `answer = 数据库里一共有 3503 首歌曲…`——同一问题、同一套图，只有语言不同。

