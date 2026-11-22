# 生产化落地计划（PRODUCTION）

本文件是「把 `sql-agent-LangGraph-advanced` 从 notebook 验证阶段推进到**可分享的作品集链接**」的落地计划，
记录目标、约束、决策（D1–D24）、改动清单、实施批次与验收证据。

与现有文档的关系：

| 文档 | 职责 |
|---|---|
| `DESIGN.md` | 记录**进阶版**（会话图 / 持久化 / 人工介入 / 时间旅行）的设计取舍与**四条硬不变量** |
| `FEATURES.md` | 记录项目体现了哪些 LangGraph / LangChain 框架能力，以及**刻意未覆盖**的部分 |
| `README.md` | 怎么跑（CLI / pytest / notebook） |
| **`PRODUCTION.md`（本文件）** | **怎么上线**：边界层、部署形态、验收证据 |

**冲突裁决顺序**：凡本文件与 `DESIGN.md` §4 冲突，以 §4 为准——唯一例外是批 2 的「受控例外」（见 §4）。

---

## 0. 目标与边界

**目标**：一个可公开分享的网页链接，任何人打开即可用自然语言查询 chinook 数据库；全部场景由代码自动路由，不依赖人工在 notebook 各格之间挑着跑。

**已定的硬边界**（不可越界，否则方案作废）：

- 前端**零构建**：单文件 `server/static/index.html`，无框架、无 npm、无 CDN 依赖；
- 运行形态：**一个 Docker 镜像 + 单进程 + 单实例**，`uvicorn --workers 1`；
- 存储：**本地 SQLite 文件**，无 Postgres / Redis / 对象存储；
- 数据：**仅 chinook**（公开示例库），不接真实业务库；
- 唯一外部依赖：**LLM API**（DeepSeek / Qwen）。

---

## 1. 决策记录（D1–D24）

| # | 决策 | 结论与理由 |
|---|---|---|
| **D1** | 上线目标与受众 | **(a) 可分享的作品集链接**。低成本改造优先；不做账号体系。(a) 是内测产品与私有化交付的真子集，日后可平滑升级，反之拆不掉 |
| **D2** | 硬约束 | 零构建前端 · 单机 SQLite · Docker 单实例 · 无外部服务（见 §0）。整条链只有 Python 一种语言、一个进程 |
| **D3** | 数据边界 | 仅 chinook（公开示例库）。但**假设未知用户一定会滥用**——因此仍需只读账号 + 配额 + 超时 |
| **D4** | 资产与仓库 | `sqlagent/` 核心库不动（只在 `server/` 加壳）；notebook **降级为教学文档**、不再承担验证职责；`cli.py` 保留为开发者调试入口；仓库**原地扩展**（`server/` 已在），部署文件与 CI 同仓 |
| **D5** | 单一入口自动分支（硬需求） | `in / out / ambiguous / sql_review(approve·edit·reject) / clarify` **全部由代码路由**，不允许人工在 notebook 各格之间挑着跑。这是本次验收的核心判据 |
| **D6** | 错误处理总纲 | **分层**：可预期失败（LLM 超时/429/解析失败、SQL 失败、改写到上限、中断续接错配）→ 降级为可读话术并标 `degraded`；不可预期失败 → 500 + `trace_id`。**任何用户可见失败都必须落一条结构化日志**（消掉分层带来的"隐藏 bug"风险） |
| **D7** | 交互形态 | **维持同步 JSON**（不上 SSE）。理由：`interrupt()` 本身是非流式语义的暂停点，SSE 收益在观感而非正确性，却要把前端从 6 行 fetch 变成状态机 |
| **D8** | 状态存放 | **一个 SQLite 文件**同时承载 checkpointer（`SqliteSaver`）+ 会话元数据表 + 配额计数；`session_id`（对外、可轮换）与 `thread_id`（对内、图主键）**解耦**；**7 天**未访问即清理 |
| **D9** | 验收证据 | 75 项既有 pytest 全绿（底线）+ **API 层分支测试**（覆盖 D5 五种分支）+ **故障演练测试**，三者必做；成本快照脚本必做；浏览器 E2E 选做 |
| **D10** | 准入控制 | **共享口令**（`APP_PASSCODE`，校验后下发 httpOnly cookie）+ **每日全局配额**。作品集链接会漂到全网，口令让你能把链接发给 3 个面试官而不是全网，配额让最坏损失可算 |
| **D11** | 阈值清单 | LLM 单次 `request_timeout=30s`（重试保持 `max_retries=3`）· 整轮**不设硬超时** · SQL `MAX_ROWS=1000`（超出截断并标注）· `MAX_CLARIFY=3` · `message ≤ 500` 字、body ≤ 8KB · 每日全局 200 轮 / 单会话 20 轮 · 会话 7 天过期 |
| **D12** | 超时语义 | 同步 `invoke()` **没有取消原语**，故超时的正确语义是**前端放弃等待**而非服务端回滚：只堵会真挂死的点（LLM 单次 30s），服务端跑完把检查点落库，前端 120s 放弃并提示「刷新可取回」 |
| **D13** | DB 第二道防线 | 部署时把 `DB_URL` 设为 SQLite URI `sqlite:///file:/app/data/chinook.db?mode=ro&uri=true`。**只改环境变量、不动 `db.py`**（符合 D4）；此后 `is_readonly()` 的定位从「安全边界」降级为「早退优化」 |
| **D14** | 观测与审计 | **JSON 行日志**（每轮一条：`trace_id / session_id / route / status / degraded / 各阶段耗时 / LLM 调用次数 / SQL / 行数 / 错误类名`）+ **SQLite 审计表**（每轮一行，供成本快照脚本读） |
| **D15** | API 响应契约 | 外面包一层壳：`{session_id, trace_id, route, status, degraded, events: [...], trace: {...}}`；`trace` 的 11 个键**逐字不变**。`events` 现在放一条 `{"type":"final"}`，日后换 SSE 形状可平滑演进 |
| **D16** | 部署收口 | **裸 uvicorn 路线**（批 4 定稿）：一条 `uvicorn ... --workers 1` 起服务 + `systemd` 常驻 + `Caddy` 反代拿 HTTPS + `.env` 逐级查找（**不进镜像、不入库**）；`/health` 增加 `version` 与状态库可写性探测；补 **10 并发 × 20 轮**压测。**不做容器**：本轮目标只是「可分享的作品集链接」，依赖已由 `requirements.txt` 钉住、数据只有 `chinook.db`（只读）与一个状态 SQLite 文件，Docker 只增加一层运维面 |
| **D17** | 前端 UI 状态清单 | ① 口令页 ② 提问中：转圈 + **已等待秒数计时器** ③ 五种中断卡片（复用已有 `renderInterrupt` / `renderHitl`）④ 错误条（`degraded` / 500+trace_id / 120s 放弃 三种措辞）⑤ 路线选择器 + 人工复核勾选（已有）⑥ 历史渲染 + 清空/新建 ⑦ 示例问题按钮 ×3 |
| **D18** | 失败话术 | 统一收在 **`server/messages.py`**（不进 `sqlagent/`，守住 D4）；全中文、每条 ≤ 2 句、**每条都附「你可以这样问」的示例**。覆盖：范围外 / 澄清超限 / 改写超限 / LLM 超时 / 主+兜底模型全挂 / 配额用尽 / 口令错误 / SQL 被守卫拦下 / 数据库文件缺失。**批 1/批 2 实施后按落点收敛**：核心库自己的业务分支话术留在核心库（`OUT_OF_SCOPE_TEMPLATE` / `FAIL_ANSWER` / `CLARIFY_LIMIT_ANSWER`），`messages.py` 只收**边界层才知道的失败**（模型侧不可用 / 会话过期 / 口令 / 配额）——否则 `sqlagent` 要反向 import `server`，层次就倒了 |
| **D19** | 实施顺序 | 切 **5 批**，每批独立可验、pytest 随时可跑（见 §4）。**批 2 与批 5 是仅有的两批会碰 `sqlagent/`**，是风险集中点。**后续文档化阶段新增批 6（双语，D24）**，同样落在 `sqlagent/`，性质与批 2 相同（只加 `lang` 附加参数） |
| **D20** | 是否重塑编排 | **不重塑**。`ConversationBI` 的会话图已用**图原语等价实现**了 handoffs 的状态驱动；改成 `wrap_model_call` 属于「为了用 API 而用 API」，且要推倒 75 项测试。handoffs 一侧**维持现状** |
| **D21** | SQL 生成抽成子代理 · 落点 | **只落在 `sqlagent/agents/`（agent 路线）**。不动 `workflow/graph.py` 的 SQL 子图 → **不碰 §4 四不变量**，75 项 pytest 一条不用改。graph 路线**明确不做**（它的上下文本来就被 `scope → tables → schema_context` 切成有界小块，没有这个痛点），写进 README 的「下一步」 |
| **D22** | 子代理的上下文边界 | **只给问题文本**，让子代理自己用 `list_tables` / `parse_table` 探索 schema（等价官方文档的 `mode: "isolated"`）。理由：把 schema 一次性喂进去等于把「探索」留在父上下文，疼点原封不动；多花的 1–2 次 LLM 调用正是我们要买的隔离 |
| **D23** | 子代理的输出契约 | 回**结构化摘要** `{sql, tables, attempts, note}`（用已有 `invoke_structured_messages` + Pydantic，零新依赖）。父 context 只留一条摘要，但 `trace.sql` / `trace.attempts` 逐字不变（保住 D15 与既有测试），自纠轨迹落进 D14 审计表。**子代理内部不允许 `interrupt()`**，人工闸门仍留在父图 |
| **D24** | 中英双语（默认英文） | **网页默认英文、全链路可切中文**。做法：把语言做成**附加参数 + 默认值**——核心库与 API 一律 `lang: str = "zh"`，故 `DESIGN.md` §4 四条不变量**逐字成立**（`run_workflow` 原签名不动，内部转调 `_run_workflow(..., lang="zh")`）；网页层默认 `LANG='en'`。语言是**会话属性**（`sessions.lang` 落库），提示词在**建会话时**按 `lang` 选定；`X-Lang` 请求头只在「还不知道是哪个会话」的失败（口令 / 500 / 404）里兜底。**切换语言 = 新建会话**（提示词已定，只换 UI 文案不能换「回答的语言」）。文案单一事实源 = `sqlagent/i18n.py` + `server/messages.py`；只翻 4 条**产出自然语言**的提示词，选表 / 生成 SQL / 改写不翻 |

---

## 2. 目标形态

```
浏览器（零构建单文件 index.html）
   │  fetch JSON（D15 envelope）
   ▼
┌──────────────────────────────────────────────────────┐
│ FastAPI · server/app.py（单进程 · --workers 1）      │
│  口令 → 配额 → 日志/审计 → 异常兜底 → trace_id       │  ← 边界层（本次主要改造面）
│  POST /api/session · /api/chat · /api/chat/resume    │
│  GET  /api/session/{id} · /health · /                │
└──────────────────────────────────────────────────────┘
   │ 同进程函数调用，无网络跳（不是微服务）
   ▼
┌──────────────────────────────────────────────────────┐
│ sqlagent（核心库，行为由 DESIGN §4 锁住）            │
│  ConversationBI → 会话图（graph 路线）                │
│  AgentBI → create_agent（agent 路线）                 │
│     └─ write_sql 子代理 ← 本次唯一新增的编排单元      │
└──────────────────────────────────────────────────────┘
   │
   ▼
一个数据卷：
  chinook.db            只读挂载（D13: mode=ro）
  checkpoints.sqlite    读写：检查点 + 会话元数据 + 审计表（D8 / D14）
```

---

## 3. 改动清单（按落点分三类）

### A. 只动 `server/`（零风险，不碰核心库）

| 文件 | 改动 |
|---|---|
| `server/app.py` | D15 envelope · D6 异常兜底 + `trace_id` · D10 口令校验 · 配额 · D14 JSON 日志 · `lifespan` 内换 `SqliteSaver` + 会话元数据表 · `/health` 增强 · **D24 `lang` 贯通**（`SessionIn.lang` → `sessions.lang` → `_build_bot(..., lang)`；失败话术随 `X-Lang`） |
| `server/static/index.html` | D17 七项 UI 状态 · 读 envelope 新字段 · 120s 放弃 + 计时器 · 示例问题按钮 · **D24 蓝色双语切换按钮**（`#lang`，文案与当前语言**相反**）+ `localStorage` 记忆 + 请求带 `X-Lang` |
| `server/messages.py` | **新增**：D18 全部失败话术（**双语**：`_ZH` / `_EN` 两表 + `text(name, lang)`，默认 `zh`；7 个中文模块级常量**原样保留**以兼容既有测试） |
| `server/store.py` | **新增**：会话元数据表 + 审计表 + 配额的 SQLite 读写（与 checkpointer 同库不同表）· **D24** `sessions` 加 `lang` 列 + 老库自动迁移（`PRAGMA table_info` → 缺列则 `ALTER TABLE`，幂等） |
| `scripts/loadtest.py` | **新增**：10 并发 × 20 轮压测（真 uvicorn + 假模型，离线可复跑） |
| `scripts/cost_snapshot.py` | **新增**：5 轮典型对话的 token / 耗时 / 降级（真模型） |
| `.env.example` | **补充**：`APP_PASSCODE`、`STATE_DB_PATH`、只读 `DB_URL` 示例 |
| `README.md` | 补「怎么上线」章节（现在正文**完全没提** server 与上线） |

> 批 4 **不新增部署文件**：没有 `Dockerfile` / `.dockerignore`（D16 定稿走裸 uvicorn），
> systemd 与 Caddy 的配置以示例形式写在 `README.md` §8.3 / §8.4 —— 它们是「运维动作」，不是本仓库的代码。

### B. 动 `sqlagent/` —— **受控例外（批 2）**

三处都是**新增参数化上限**，且**默认路径行为意图不变**：

| 文件 | 改动 | 对 §4 的影响 |
|---|---|---|
| `sqlagent/llm.py` | `build_raw_model` 内加 `request_timeout=30` | 不破 §4 #1/#2（签名与调用次数不变）；#3 见 §7 风险 2 |
| `sqlagent/tools.py` | `run_readonly_sql` 加 `MAX_ROWS=1000` 截断 + 标注 | #1 不改返回结构（截断标注在文本里） |
| `sqlagent/conversation/graph.py` | clarify 回环加 `MAX_CLARIFY=3` + 状态字段 | 新增 state 键安全：`test_conversation_topology` 用 `<=` 子集判断 |

> 若实施中发现任何一处**必须**改变返回字段或调用次数，立即停止并回报——那属于改 `DESIGN.md` §4，需要单独裁决。

### C. 动 `sqlagent/agents/` —— 子代理（批 5，D21–D23）

| 文件 | 改动 |
|---|---|
| `sqlagent/agents/sql_agent.py` | **新增** `write_sql` 工具：内部包一个 `create_agent` 子代理（工具 = `list_tables` / `parse_table`），只在干净窗口里自纠，返回结构化摘要 `{sql, tables, attempts, note}` |
| `sqlagent/agents/session.py` | `AgentBI` 装配改用带 `write_sql` 的主 agent；`_to_trace` 的 `sql` / `attempts` 从摘要回填 |
| `sqlagent/__init__.py` · `agents/__init__.py` | 导出新名字（保持旧名字可用，§4 #4） |
| `tests/test_agent_subagent.py` | **新增**：断言主 context 不含 schema 原文、摘要字段正确回填 trace、子代理不产生 interrupt |

### D. 动 `sqlagent/` —— **受控例外之二：双语（批 6 / 文档化阶段，D24）**

与批 2 同性质：**只加 `lang` 附加参数**，默认 `zh` 时行为逐字不变。

| 文件 | 改动 | 对 §4 的影响 |
|---|---|---|
| `sqlagent/i18n.py` | **新增**：`_TEXT`（13 条中英对照，模块级 `assert` 钉死）+ `text(name, lang)` + `normalise(lang)` | 纯新增，不动既有路径 |
| `sqlagent/prompts.py` | 原中文提示词**一字未动**；**新增** 4 条英文变体 + `summarize_prompt / scope_prompt / rewrite_followup_prompt / summarize_history_prompt(lang="zh")` 选择器（`zh` 下返回**原对象**） | #1/#2/#3 不破（选表 / 生成 / 改写**不配**英文版） |
| `sqlagent/workflow/graph.py` | 拆出 `_run_workflow(question, parser, *, verbose=True, lang="zh")`；**原 `run_workflow` 签名一字不动**，内部转发 `lang="zh"` | 直接对应 #1 |
| `sqlagent/gates.py` · `conversation/graph.py` · `conversation/bi.py` · `chatbi.py` · `agents/sql_agent.py` · `agents/session.py` · `cli.py` | 各入口加 `lang: str = "zh"` 并透传；`OUT_OF_SCOPE_TEMPLATE` / `FAIL_ANSWER` / `CLARIFY_LIMIT_ANSWER` **名字与中文内容逐字保留**（值改从 `i18n` 取）；CLI 新增 `--lang {zh,en}` | #1/#4 不破 |
| `tests/test_i18n.py` | **新增**：39 项（文案层 / 提示词选择器 / 闸门话术 / 语言贯通 / 落库迁移 / CLI / API） | 只加测试，不改既有预期 |

> 与批 2 一样：若发现某处**必须**改返回字段或调用次数，立即停止——那属于改 `DESIGN.md` §4。

---

## 4. 实施批次与每批验收

每批结束都必须：`pytest` 全绿 + 该批新证据可复现。

| 批 | 内容 | 验收 |
|---|---|---|
| **批 1** | `server/` 硬化：只读 `DB_URL`（D13）· JSON 日志 + `trace_id`（D14）· 全局异常兜底（D6）· envelope（D15） | 75 项 pytest 全绿（`sqlagent/` 未动）· 手工验证 out / clarify / sql_review / clarify-agent 五种分支同一入口自动分流（D5） |
| **批 2** | 阈值落地：`request_timeout` · `MAX_ROWS` · `MAX_CLARIFY`（D11）**+ 各自单测** | 75 项 + 新增单测全绿；**逐条确认三处未破 §4** |
| **批 3** | 状态与准入：`SqliteSaver` 替换 `InMemorySaver` · 会话元数据表 + 7 天清理（D8）· 口令（D10）· 配额（D11）· 审计表（D14） | 重启进程后旧 `session_id` 仍可续问 · 口令错误 → 401 · 配额用尽 → 提示话术 · 审计表有本轮记录 |
| **批 4** | 部署收口：`/health` 增强（D16）· 一条命令起服务的上线说明（systemd + Caddy）· 压测脚本 · 成本快照脚本 · README 补章 | 一条命令起服务并给出上线说明 · **10 并发 × 20 轮压测通过** · 成本快照输出 token / 耗时 / 失败率 |
| **批 5** | SQL 生成子代理（D21–D23），**仅 agent 路线** | **已完成**：新增子代理测试 **9 项全绿** ✅ · 既有 102 项**一条未改** ✅ · 同一问题下主 context 长度显著小于改造前（真模型实测约 **7 倍缩小** ✅，但输入 token **+42%** —— 口径见 §7 风险 6） |
| **批 6** | 中英双语（D24，**文档化阶段新增**）：`i18n.py` · 提示词英文变体与选择器 · 核心库 / 边界层 / 前端 `lang` 贯通 · `test_i18n.py` | **已完成**：新增双语测试 **39 项全绿** ✅ · 既有 111 项**一条未改** ✅ · 真模型（`deepseek`）中英各一轮实测：同一问题英文会话答英文、中文会话答中文（见 §5 第 8 条） |

---

## 5. 验收证据清单

1. **回归**：基线 75 项既有 pytest 每批后跑一次，**一条未改**；批 5 结束共 **111 项全绿**
   （75 基线 + 批 1/2/3/4/5 新增 36 项）；**批 6（双语）结束后共 150 项全绿**（+ 39 项）。
2. **分支覆盖（D5 的证据）**：`TestClient` 打 6 条路由，`route=graph` 与 `route=agent` 各覆盖 `in / out / ambiguous / sql_review / clarify`；显式断言「同一入口自动分流」。
3. **故障演练**：拔掉 API Key → 降级话术 · 写 SQL → 被守卫拦下 · `resume` 的 decisions 数量错配 → 友好报错 · 口令错误 → 401 · 配额用尽 → 话术。
4. **压测**：10 并发 × 20 轮（结论回写 §7 风险 1 与本条）。**批 4 已跑**：`scripts/loadtest.py`（真 uvicorn + 假模型，离线）
   ```
   200 轮全部 200；墙钟 4.88s（40.9 轮/秒）
   单轮延迟 P50 192ms / P95 555ms / max 564ms
   /health 探测 60 次、全部 ok；审计表 200 行；`database is locked` 0 次；`unhandled` 0 次
   额度边界：第 201 轮 → 429（并发下也没多发一格）
   ```
5. **成本快照**：脚本跑 5 轮典型对话，输出每轮 token / 耗时 / 是否降级，落进文档。**批 4 已跑**
   （`scripts/cost_snapshot.py`，真模型 `deepseek`，5 轮全 `done`、0 降级）：
   ```
   | 轮 | LLM 调用 | 输入 token | 输出 token | 耗时 ms |
   | 1 | 4 |  1877 | 143 |  5383 |
   | 2 | 5 |  2100 | 807 |  9510 |
   | 3 | 5 |  2598 | 356 |  7148 |
   | 4 | 5 |  2659 | 880 |  9544 |
   | 5 | 5 |  2507 | 531 |  7984 |
   合计 24 次调用 · 输入 11741 / 输出 2717 tokens · 每轮 5.4–9.5s
   ```
   口径说明：**5 轮 ≈ 1.2 万输入 / 0.27 万输出 token**，即单会话额度上限（20 轮）约 4–5 万 token；
   「每日全局 200 轮」按同一口径是数十万 token 量级 —— 最坏损失因此是可算的（D10 的初衷）。
   金额不由本文件给出：单价随 provider 调价，脚本留了 `--price-in / --price-out`（元/百万 token）现算。
6. **批 5 子代理实测**（真模型 `deepseek`，`tests/test_agent_subagent.py` 9 项 + 探测脚本）：
   ```
   | 指标（同一问题序列 3 轮）      | 改造后（子代理） | 改造前（原 agent） | 方向 |
   | 主 context 长度（字符）        | 279→549→725      | 4828→4943→5146     | ≈ 7 倍缩小 ✅ |
   | 输入 token 累计                | 16818            | 11830              | +42% ❌ |
   | LLM 调用次数                   | 19               | 8                  | +11 次 ❌ |
   ```
   单轮抽查「哪个国家的客户数量最多？」：模型**主动**用了 `write_sql`、SQL 正确（`USA` / 13），
   主 context **250 字符** vs 改造前 **4808 字符**。结论：**context 正收益、token 负收益**，
   根因是子代理每轮全新窗口需从头探 schema。**用户裁决保留**（D22 买的就是隔离），
   回退判据与后续改进见 §7 风险 6。
7. **并发压测与成本快照脚本**均可重跑：`scripts/loadtest.py` 全离线（假模型，零成本）；
   `scripts/cost_snapshot.py` 用真模型（要花 token，故分离成两份）。E2E（Playwright）选做。
8. **双语实测（批 6 / D24，真模型 `deepseek`）**：`python -m uvicorn server.app:app --port 8123` 后 `curl` 直打 API
   ```
   GET  /                → <html lang="en">、<button id="lang" …>中文</button>、const DEFAULT_LANG='en'
   POST /api/session     → body 带 "lang":"en" 时回 "lang":"en"；无 body 时回 "lang":"zh"
   GET  /api/session/xxx → 404 + X-Lang: en → 英文话术；不带该头 → 中文话术
   英文会话一轮 → status done · sql "SELECT COUNT(*) FROM tracks;" · answer "There are 3,503 tracks in the database."
   中文会话一轮 → status done · answer "数据库里一共有 3503 首歌曲（tracks 表共 3503 条记录）。"
   ```
   同一问题、同一套图，只有语言不同；`pytest` 全量 **150 passed**（75 基线 + 36 上线硬化/子代理 + 39 双语）。

---

## 6. 明确不做（non-goals）

- 账号体系 / 多租户 / SSO / 权限隔离；
- SSE 流式（仅预留 `events` 形状）；
- Cloudflare Workers（V8 isolate，跑不了这套 Python 栈，也没有本地文件系统放 SQLite）——最多用来套域名或前置 CDN；
- 多 worker / Postgres / Redis（违反 D2）；
- 改 graph 路线的 SQL 生成（D21）；
- 前端框架、构建链、CDN 依赖（D2）；
- 动 `DESIGN.md` §4 的四条硬不变量（除批 2 的受控例外）。

---

## 7. 风险与未验证项

| # | 风险 | 判据 / 处置 |
|---|---|---|
| 1 | **并发安全未验证**：FastAPI 同步路由跑在线程池里，而 `_state["parser"]` 是跨线程共享的单例，SQLAlchemy 对 SQLite 文件库的连接池 + `sqlite3` 的 `check_same_thread` 组合在本项目**从没被并发验证过** | **批 4 已实测通过**：`scripts/loadtest.py` 真起 uvicorn（真线程并发，非 `TestClient`）+ 假模型，10 并发 × 20 轮 = **200 轮全部 200**，墙钟 4.88s、P95 555ms，日志里 `database is locked` **0 次**、`unhandled` **0 次**；共享 `parser` 单例 + 共享 SQLite 连接 + 线程池的组合没炸，**无需** `connect_args={"check_same_thread": False}` 或 per-thread engine（D2 不变量保持） |
| 2 | `MAX_ROWS=1000` 可能改变既有测试或 notebook 的输出（chinook 的 `playlist_track` ≈ 8.7k 行，若某测试取全表会命中截断） | **批 2 已实测**：既有测试的 SQL 全是 `COUNT(*)` 一类聚合，无全表取数 → 90 项全绿、一条既有预期未改；截断行为由新增 `test_run_readonly_sql_truncates_large_result`（`playlist_track` = 8715 行）钉住。另：`MAX_ROWS` 只作用于 `run_readonly_sql()` 返回的**文本**，graph 路线的 `trace.rows` 走 `parser.execute_sql`，口径未被动过 |
| 3 | `mode=ro` 下 `DBParser` 的反射与采样需实测（理论上只读，但 SQLAlchemy 可能建临时表） | **批 1 已实测通过**：`sqlite:///file:…?mode=ro&uri=true` 下反射出 11 张表、`check_sql` / `execute_sql` 正常、`create table` 被数据库拒绝（`OperationalError`）。无需退回备选 |
| 4 | 会话元数据 / 审计表与 checkpointer 同库 → 写锁竞争 | **批 3 已上缓解**：状态库统一 `PRAGMA journal_mode=WAL`（文件级，对同库所有连接含 `SqliteSaver` 生效）+ `busy_timeout=5000`；边界层只建**一个**共享检查点连接（`make_checkpointer` 每次调用都新开连接，多连接才容易撞锁）。**批 4 压测实测 200 轮 `database is locked` 0 次**（压测首轮确实炸过一次，但根因不在"同库写锁竞争"：`/health` 的可写性探测绕过了 `Store.lock` 在共享连接上真写一次 `PRAGMA`，与其它线程写操作撞车——已在 `Store.writable()` 里补锁修复），故**不拆库** |
| 5 | `SqliteSaver` 下长会话的 `list_hops()` 性能（`/api/session/{id}` 会全量取检查点） | **批 3 已观测，无需处置**：20 轮真实会话（即单会话额度上限）= 140 跳检查点，`list_hops()` 连续三次 **9.9 / 8.4 / 9.1 ms**，状态库 **1188 KB**。结论：**不给 `trajectory` 加上限**；单会话 20 轮的上限本身就框住了这里的绝对量 |
| 6 | 子代理每轮都是**全新窗口**（D22 只给问题文本），必须重新探索 schema → 可能多花 LLM 调用与 token | **批 5 已实测（真模型 `deepseek`）**：同一问题序列 3 轮，主 context 279 → 549 → 725 字符 vs 改造前 4828 → 4943 → 5146 字符（约 **7 倍缩小**）、`attempts=1`、SQL 全对；但输入 token **16818 vs 11830（+42%）**、LLM 调用 **19 vs 8** —— **context 为正收益、token 为负收益**。根因：旧路线只在第 1 轮探索一次 schema，之后复用；子代理每轮独立。用户裁决：**保留**（D22 买的隔离是目的，不回退批 5）。下一步（未做）：**子代理会话级复用探索窗口**、**父图先把指代式追问改写成自包含问题再交给子代理**（D22 只给问题原文，子代理看不到上文） |
| 7 | **切换语言会新建会话**：图 / Agent 的提示词在**建会话时**就已按 `lang` 编译进状态图，仅换 UI 文案无法让旧会话的「回答语言」跟着变 | **设计取舍（D24，已在 `DESIGN.md` §6 承认）**：前端按下蓝色按钮时自动 `newSession(...)`，并提示「已切换 · 已新建会话」。旧会话在状态库中保留到 7 天过期，只是界面不再指向它；如需保留历史体验，可后续做「按 `session_id` 切换语言」（需重建 bot 并重放检查点，本轮不做） |
