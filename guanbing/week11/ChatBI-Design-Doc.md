# ChatBI 设计文档

> **版本** v1.0 · 2026-11-10 · 基于 `sql-agent-workflow.ipynb`（43 个 cell）· 模型 `deepseek-v4-flash` · 数据源 `chinook.db`
>
> 本 demo 为演示性质，**已验证 ≠ 生产就绪**；生产化路径见 [§3.12 演进规划](#312-演进规划gate-05)。

## 目录

**一、项目设计文档**

- [1.1 背景与问题](#11-背景与问题)
- [1.2 项目目标](#12-项目目标)
- [1.3 目标用户与典型场景](#13-目标用户与典型场景)
- [1.4 范围与边界（含 Non-Goals）](#14-范围与边界含-non-goals)
- [1.5 交付物与验收标准](#15-交付物与验收标准)
- [1.6 里程碑](#16-里程碑)
- [1.7 术语与名词对照](#17-术语与名词对照)

**二、功能设计文档**

- [2.1 能力总览](#21-能力总览)
- [2.2 三道闸门](#22-三道闸门)
- [2.3 7 阶段流水线](#23-7-阶段流水线)
- [2.4 用户交互三种入口](#24-用户交互三种入口)
- [2.5 结果渲染](#25-结果渲染)
- [2.6 问题模拟生成器](#26-问题模拟生成器)
- [2.7 逐层裁判验证装置](#27-逐层裁判验证装置)
- [2.8 功能验收点汇总](#28-功能验收点汇总)

**三、技术设计文档**

- [3.1 系统全景与组件关系](#31-系统全景与组件关系)
- [3.2 技术选型](#32-技术选型)
- [3.3 模块与接口契约](#33-模块与接口契约)
- [3.4 数据访问层与执行链路](#34-数据访问层与执行链路)
- [3.5 提示词与防御式设计](#35-提示词与防御式设计)
- [3.6 安全与脱敏](#36-安全与脱敏)
- [3.7 验证与评测](#37-验证与评测)
- [3.8 Notebook cell 索引](#38-notebook-cell-索引)
- [3.9 快速开始（复现）](#39-快速开始复现)
- [3.10 仓库结构与交付物](#310-仓库结构与交付物)
- [3.11 常见改动指引（How-To）](#311-常见改动指引how-to)
- [3.12 演进规划（Gate 0–5）](#312-演进规划gate-05)
- [3.13 已知限制与技术债](#313-已知限制与技术债)

---

# 一、项目设计文档

## 1.1 背景与问题

**要解决的问题**：让不懂 SQL 的业务人员，用一句中文把「数据库里有多少张专辑」这类问题问出来，
并拿到**可信**的答案 —— 而不是一个「看起来像答案」的答案。

**真正的难点不在「生成 SQL」，而在下面三件事**：

1. **可信**：模型吐出来的 SQL 必须真的在数据库上跑，结果必须真的来自数据库，不能是模型编的数字；
2. **不乱来**：万一模型写出一条 `DELETE`，系统必须在它到达数据库**之前**就拦住；
3. **不错答**：问题超出数据库范围（「明天天气怎么样」）或意图不完整（「有多少？」）时，
   系统要**知道自己不知道**，而不是硬编一条 SQL 蒙一个答案。

传统做法把这三件事都寄托在提示词上（「你只能写 SELECT」「不确定就说不确定」）——
这是**概率性约束**，模型可能不听。本项目的核心主张是：
**把这三件事做成代码结构，而不是提示词里的请求**。

## 1.2 项目目标

四条可验收的目标（每条都在第 3 章给出实测证据）：

| # | 目标 | 可验收的表述 |
|---|---|---|
| G1 | **真的连库跑 SQL** | 存在一条从「模型吐文本」到「读 chinook.db 文件」的完整链路；用原生 `sqlite3` 直连同一文件可交叉验证出相同结果 |
| G2 | **结构性只读护栏** | 非 `SELECT`/`WITH`、多语句、写关键字 → **在到达数据库之前**被代码拦下；不依赖提示词 |
| G3 | **多轮对接用户** | 支持追问改写、范围判定、三分支（答 / 反问 / 谢绝），并维护会话历史 |
| G4 | **可被逐层检验** | 有一套「冻结验证集 + 5 层裁判」装置，能定位「哪一题、哪一步、错在哪」 |

## 1.3 目标用户与典型场景

| 用户 | 关心什么 | 在本系统里得到什么 |
|---|---|---|
| **业务方 / 数据分析初学者** | 「我不想学 SQL，我就想知道答案」 | 自然语言提问 → 表格 / 图表 / 一句话结论；越界问题被礼貌谢绝而不是胡说 |
| **SQL Agent 开发者** | 「我要照着改、照着扩」 | 确定性的 7 阶段流水线，每阶段输入输出可断言、可单步调试；换库只改一处 |
| **评审 / 验收者** | 「这东西靠不靠谱，凭什么信」 | 逐层裁判明细表 + 双引擎交叉验证的执行链路，把「感觉」变成「可定位的证据」 |

**三条典型场景走查**

1. **单轮直答**：`数据库里一共有多少首歌曲？` → [1] 解析 → [2] 选 `tracks` → [3] 生成
   `SELECT COUNT(*) FROM tracks` → [4] 守卫放行 → [6] 执行 → `3503` → [7] 汇总成自然语言。
2. **多轮追问**：`销量最高的 5 个流派是什么？` → 答完；用户追问 `那德国呢？`
   → [A] 追问改写为自包含问题（补回「发票国家是德国的…」）→ 继续走流水线。
3. **越界拦截**：`明天北京的天气怎么样？` → [B] 判定 `out` → 谢绝，并提示「可以问销量 / 客户 /
   员工 / 曲目 / 专辑 / 流派」——**不生成任何 SQL**。

## 1.4 范围与边界（含 Non-Goals）

**做什么（In Scope）**

- 单库（SQLite 文件）上的**只读问答**：统计、筛选、连接、分组、TopK；
- 完整链路：环境准备 → 解析 → 选表 → 生成 → 校验 → 改写 → 执行 → 汇总；
- 面向用户的对接层：多轮对话、范围判定、结果渲染（表格 + 自动柱状图）；
- 面向质量的评测层：问题模拟生成器 + 冻结验证集 + 5 层裁判；
- 面向未来的可迁移性设计：连接串入口 + Go-out 路线（Gate 0–5）+ 接入指南文档。

**Non-Goals（明确不做，避免被误读为「漏做」）**

| # | 不做的事 | 理由 |
|---|---|---|
| N1 | **不接入外部真实业务库** | 本阶段只到 SQLite 示例库；迁移路径已规划（[§3.12](#312-演进规划gate-05)）但**不实施** |
| N2 | **不做 Streamlit / FastAPI 产品化外壳** | 交互停在 notebook 层（ipywidgets / `input()`）；`ChatBI.ask()` 已按「可抽为后端」编写 |
| N3 | **不做「反问 → 等待 → 续接」的澄清状态机** | `ambiguous` 分支只给单轮反问，用户下一条消息自行接续 |
| N4 | **不用 ORM** | 产品本身就是「一条只读 SQL」，ORM 的对象映射用不上（详见 [§3.2](#32-技术选型)） |
| N5 | **不做大库的惰性统计与候选表检索** | 当前 `DBParser` 全库预统计，小库无感；大库优化属 Gate 4，待做 |
| N6 | **不做写操作（增删改）** | 这是能力边界，也是安全边界：系统在结构上只能是只读的 |

## 1.5 交付物与验收标准

**交付物（同目录 5 个文件）**

| 文件 | 作用 |
|---|---|
| `sql-agent-workflow.ipynb` | **主交付物**：43 个 cell，可 `nbconvert --execute` 无头跑通 |
| `README.md` | 架构说明 + 关键设计点 + 选型 + 数据来源 + 已知限制 |
| `外部数据库接入指南.md` | 换库/接外部库的 Gate 0–5 路线、方言对照、建只读账号、冒烟自检 |
| `ChatBI-Design-Doc.md` | 本文档 |
| `.env.example` | 环境变量模板（不含真实密钥） |

（`chinook.db` 为数据文件，随交付物一同提供。）

**验收标准**

| # | 验收线 | 判据 |
|---|---|---|
| A1 | 无头可执行 | `jupyter nbconvert --to notebook --execute --inplace sql-agent-workflow.ipynb` 全 notebook 无 error |
| A2 | 真的在跑 SQL | 模型输出 SQL → `execute_sql` 返回 `tracks=3503`，与原生 `sqlite3` 直连结果一致 |
| A3 | 护栏是结构性的 | `is_readonly("DROP TABLE albums;")` → `(False, ...)`；且 `run_workflow` 在守卫失败时**直接 return**，不调 `execute_sql` |
| A4 | 三分支按预期 | `in` 正向题作答；`ambiguous`（「有多少？」）反问；`out`（写诗/天气）谢绝 |
| A5 | 逐层可定位 | 12 题跑完输出「逐题逐层明细表 + 汇总指标 + 失败定位」 |
| A6 | 源码脱敏 | notebook 与文档中**无明文 Key**、**无本机绝对路径** |

## 1.6 里程碑

**已完成（对应 README「任务规划」14 项全勾）**

环境准备（脱敏加载 `.env`）· `DBParser` + `is_readonly()` · `chat()` 封装与四个阶段函数 ·
`run_workflow()` 编排（含改写回环）· 交互 Demo · 问题模拟生成器 · 小型评测 ·
ChatBI 多轮 + 自动柱状图 · `scope_check()` 三分支 · ipywidgets 不阻塞输入 ·
`nbconvert --execute` 实跑验证 · 结构性取证 · 逐层裁判验证（L0–L5）。

**未来**：Gate 0 → 5 分阶段演进路线，详见 [§3.12](#312-演进规划gate-05)（本节不重复）。

## 1.7 术语与名词对照

| 名词 | 含义 |
|---|---|
| **DBParser** | 数据库解析器：产出表清单、字段统计、外键关系、样例数据；并负责 `check_sql` / `execute_sql` |
| **ChatBI** | 面向用户的多轮入口：`ask()` 串起「追问改写 → 范围判定 → 流水线」，维护最近 5 轮历史 |
| **run_workflow** | 7 阶段流水线的编排函数，返回带 `trace` 的结果字典 |
| **is_readonly()** | 结构性只读守卫：去注释/字符串后，要求首词为 `SELECT`/`WITH`、禁多语句、禁写关键字 |
| **三道闸门** | [A] `rewrite_followup` 追问改写 → [B] `scope_check` 范围判定 → [C] 三分支 |
| **scope** | 范围标签：`in`（可答）/ `out`（越界）/ `ambiguous`（需澄清） |
| **GOLDEN_SET** | 冻结验证集：12 题，含问题、期望 `scope`、手写 `ref_sql`、固化 `ref_rows` |
| **ref_rows / ref_sql** | 权威结果集 / 权威 SQL；由人编写，经双引擎交叉验证后固化，评测时只读 |
| **freeze_golden()** | 重建 `ref_rows`：SQLAlchemy 与原生 `sqlite3` 双引擎交叉验证，不一致立即报错 |
| **JudgeAgent** | 逐层裁判：对同一道题的 `trace` 依次跑 L0–L5，聚合出端到端结论 |
| **L0–L5** | 5 个判层：闸门 / 选表 / 生成 / 校验 / 执行 / 汇总 |
| **Structure vs Prompt** | 「护栏做进代码结构」vs「护栏写在提示词里」的对照主张 |
| **Gate 0–5** | 从本地 SQLite 演进到外部真实业务库的 6 级路线 |
| **trace** | 一次问答的轨迹字典：问题、选中表、SQL、尝试次数、结果列/行、答案等 |

---

# 二、功能设计文档

## 2.1 能力总览

| # | 能力 | 入口 | 输入 | 输出 |
|---|---|---|---|---|
| F1 | 自然语言 → SQL → 答案 | `ChatBI.ask()` / `run_workflow()` | 一句中文问题 | 表格 / 图表 + 自然语言答案 |
| F2 | 追问改写（自包含化） | `rewrite_followup()` | 追问 + 会话历史 | 不依赖上下文的独立问题 |
| F3 | 范围判定 | `scope_check()` | 独立问题 + 表清单 | `{scope, reason, tables, clarify}` |
| F4 | 选表 | `select_tables()` | 问题 + 表清单/字段 + 外键 | JSON 表名列表 |
| F5 | SQL 生成 | `generate_sql()` | 问题 + 选中表 schema/样例 | 单条 `SELECT`/`WITH` |
| F6 | 只读守卫 | `is_readonly()` | SQL 文本 | `(True,"ok")` / `(False,原因)` |
| F7 | 试跑校验 | `DBParser.check_sql()` | SQL 文本 | `(ok, err)` |
| F8 | 失败改写（回环） | `rewrite_sql()` | 问题 + 原 SQL + 错误 + schema | 修正后的 SQL（≤3 次） |
| F9 | 执行 | `DBParser.execute_sql()` | SQL 文本 | `columns, rows` |
| F10 | 结果汇总 | `summarize()` | 问题 + SQL + rows | 自然语言答案 |
| F11 | 结果渲染 | `render_result()` | 结果字典 | 表格（必要时自动柱状图） |
| F12 | 造题（测试数据） | `simulate_question(s)` / `simulate_out_of_scope()` | 表 / 风格 / 数量 | 带 `ref_sql` 与真值的问题 |
| F13 | 逐层裁判 | `JudgeAgent.judge()` | 题目 + `trace` | 逐层结论 + 端到端判定 |
| F14 | 验证集冻结 | `freeze_golden()` | `GOLDEN_SET` | 双引擎固化后的 `ref_rows` |

## 2.2 三道闸门

**这张图在说什么**：用户的问题在**生成 SQL 之前**要过三道闸门，先决定「该不该答、答什么」，
只有 `in` 才进流水线。

```
                        用户输入
                           │
                           ▼
        ┌──────────────────────────────────────┐
        │ [A] rewrite_followup(question, history)│
        │     有历史？                            │
        └───────────────┬──────────────────────┘
                        │  ├─ 无历史 → 原样通过
                        │  └─ 有历史 → 交 chat() 改写成自包含问题
                        ▼
        ┌──────────────────────────────────────┐
        │ [B] scope_check(standalone, parser)   │
        │     只输出 JSON：                       │
        │     {scope, reason, tables, clarify}   │
        └───────────────┬──────────────────────┘
                        │  ★ 结构性兜底：
                        │    声称 in 但 tables 落不到任何真实表
                        │    → 强制降级 ambiguous（避免硬编 SQL）
                        ▼
        ┌───────────────┼───────────────┐
        │ out           │ ambiguous     │ in
        ▼               ▼               ▼
   ┌─────────┐    ┌────────────┐  ┌──────────────────┐
   │ 谢绝 +   │    │ 反问澄清    │  │ 交给 7 阶段流水线  │
   │ 提示可问 │    │ （附一个具体 │  │ → 表格 / 图表 /   │
   │ 什么     │    │  澄清示例）  │  │   自然语言分析     │
   └─────────┘    └────────────┘  └──────────────────┘
```

**关键设计**：闸门加在生成 SQL **之前**。若把范围判定放到流水线之后，越界问题已经被硬编成了
一条 SQL，再拦就晚了。

**结构兜底**：守门人只输出 JSON；代码侧再做一道校验 —— 若它声称 `in`，但 `tables` 里没有一个
能对上 `parser.table_names`，就**强制降级为 `ambiguous`**，宁可不答也不硬编。

## 2.3 7 阶段流水线

**这张图在说什么**：一条固定顺序的工程流水线；唯一的回环是「校验失败 → 改写重试」，
由 Python 控制次数（`MAX_REWRITE = 3`），不依赖模型自觉。

```
 [0] 环境准备
     find_up(".env") → 伪装成 OpenAI 兼容接口 → MODEL
     find_up("chinook.db") → DB_URL
        │
        ▼
 [1] 数据库解析  DBParser(DB_URL)
     table_names / 字段统计 / 外键关系 / 样例数据
        │
        ▼
 [2] Table 选择  select_tables(question, parser)
     LLM(问题 + 表清单+字段 + 外键关系) → JSON 候选表名
        │
        ▼
 [3] SQL 生成    generate_sql(question, tables, parser)
     LLM(问题 + 选中表 schema/样例/外键) → 一条 SELECT / WITH
        │
        ▼
 [4] SQL 校验 ◀────────────────────────────────┐
     is_readonly(sql) → parser.check_sql(sql)   │
        │                                        │
        ├─ ok=False ─▶ [5] SQL 改写 ─────────────┘
        │              rewrite_sql(问题+原SQL+错误+schema)
        │              retry ≤ MAX_REWRITE(=3)
        │              3 次仍失败 → 直接返回失败原因（不编造结果）
        │
        └─ ok=True
             │
             ▼
 [6] SQL 执行   parser.execute_sql(sql) → columns, rows
        │
        ▼
 [7] 结果汇总   summarize(question, sql, rows) → 自然语言答案
```

**各阶段输出契约**

| 阶段 | 输入 | 输出 | 失败行为 |
|---|---|---|---|
| [1] 数据库解析 | `db_url` | `table_names` / `get_table_fields()` / `get_data_relations()` / `get_table_sample()` | 建连失败 → 抛错终止 |
| [2] Table 选择 | 问题 + 表摘要 + 外键 | `["invoices", "invoice_items", ...]` | 返回空列表 → 后续生成会失败并被改写回环接住 |
| [3] SQL 生成 | 问题 + 选中表 schema | 单条 `SELECT` / `WITH` | —— |
| [4] SQL 校验 | `sql` | `(True,"ok")` / `(False, err)` | 进入 [5] 改写回环 |
| [5] SQL 改写 | 问题 + 原 SQL + 错误 + schema | 修正后的 SQL | 超过 3 次 → 放弃并诚实说明 |
| [6] SQL 执行 | `sql` | `columns, rows` | —— |
| [7] 结果汇总 | 问题 + SQL + rows | 自然语言答案 | —— |

**为什么是「确定性流水线」而不是「让 LLM 自主决定下一步」**：
每一步的输入输出可断言、可打日志、可单步调试；回环次数由 `MAX_REWRITE` 在 Python 层控制；
失败时诚实返回原因，不编造数据。

## 2.4 用户交互三种入口

notebook 里提供三种触发方式，按「产品化程度」递增：

| # | 入口 | 位置 | 特点 | 默认 |
|---|---|---|---|---|
| 1 | **交互输入框（ipywidgets）** | §9「交互输入框」cell | 输入框 + 「提问」按钮，**不阻塞内核**；`Run All` / `nbconvert` 也能跑完 | **启用** |
| 2 | **`input()` 循环** | §6 / §9 各有一次 | 把 `INTERACTIVE` 改为 `True` 后运行；无头执行时会一直阻塞 | `False` |
| 3 | **产品化外壳** | —— | 把 `ChatBI.ask()` 抽成后端函数，外套 Streamlit / FastAPI | 未做（Non-Goal N2） |

```python
# 入口 1：ipywidgets（节选，不阻塞内核）
import ipywidgets as widgets
box = widgets.Text(placeholder="输入你的问题…")
btn = widgets.Button(description="提问")

def on_click(_):
    res = bot.ask(box.value, show_trace=True)
    render_result(res)

btn.on_click(on_click)
display(widgets.HBox([box, btn]))
```

## 2.5 结果渲染

`render_result(res, max_rows=None)` 把结果字典渲染成表格，并在合适时**自动出柱状图**。

| 行为 | 规则 |
|---|---|
| 表格 | 默认 `max_rows=None`，**全部展示**；传整数则截断并提示可调大 |
| 自动柱状图 | 结果集恰为「1 类别列 + 1 数值列」且行数 `1 < n ≤ 50` 时触发 |
| 不画图 | 柱子过多（> 50）不画，避免糊成一团 |
| 中文字体 | `plt.rcParams["font.sans-serif"]` 首选 `Arial Unicode MS` / `Heiti SC` 等 |
| 抑制噪音 | `warnings.catch_warnings()` + `simplefilter("ignore")` 包住 `display` —— **缺字警告会把本机绝对路径写进 notebook 输出**，必须屏蔽 |

## 2.6 问题模拟生成器

参考实现里「对着表结构用 LLM 造题」的循环，在本实现中就是一套可复用的造题器。

| 函数 | 作用 |
|---|---|
| `simulate_question(parser, table, style, user_style)` | 对单表造 1 题；产出参考 SQL 与真值 |
| `simulate_questions(parser, tables, n_per_table, style, user_style)` | 批量造题；只保留「可执行且答案为单值」的题 |
| `simulate_out_of_scope(n)` | 造**越界负样本**（与库无关的问题），用于测试范围守门人 |

- `style` 支持 `count`（单值统计）/ `group`（分组对比）；
- `user_style=True` 会把技术口吻的题改写成普通用户会问的说法；
- `build_eval_set()` 只是它的特化调用：`simulate_questions(..., style="count")`。

> **自证偏差声明**：造题与判分用同类模型，存在自证偏差。它只用于构造测试 / 演示数据，
> **不代表真实用户分布**，其指标不能当作真实业务准确率。

## 2.7 逐层裁判验证装置

**为什么要逐层**：一次问答的错误可能注入在 5 个跳跃点；只看终点值既无法定位，
也会放过「结论对、过程错」（比如选错表却恰好数值撞对）。

**这张图在说什么**：一次问答 = 5 次跳跃；`L0–L5` 分别盯住一次跳跃，
并标明哪几层是纯代码断言、哪几层必须请 LLM。

```
   问题 ──①──▶ 选表 ──②──▶ 生成 SQL ──③──▶ 校验 ──④──▶ 执行 ──⑤──▶ 汇总回答
    │           │            │               │            │            │
    ▼           ▼            ▼               ▼            ▼            ▼
 L0_scope    L1_tables    L2_sql        L3_guard     L4_result    L5_answer
┌─────────┐ ┌─────────┐ ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌──────────┐
│ 确定性   │ │确定+LLM │ │  LLM    │  │ 确定性   │  │确定+LLM │  │LLM+数字   │
│ 标签一致 │ │多余表是 │ │ 语义    │  │ 守卫是否 │  │结果集等 │  │ 溯源      │
│         │ │否无害   │ │ 等价    │  │ 放行     │  │价/复核  │  │          │
└─────────┘ └─────────┘ └─────────┘  └─────────┘  └─────────┘  └──────────┘
      └──────────── 端到端 = 所有「有判定」的层全部通过 ────────────┘
```

**两条第一性原理**

1. **可判定 vs 需判断**：数值是否相等、SQL 能否执行、有无写操作、答案里的数字能否溯源
   —— 这些**代码断言就够，不劳 LLM**；只有「这条 SQL 是否忠实表达了问题」才需要裁判。
2. **LLM 裁判必须带参照物**：喂「问题 + 待审 SQL + 权威 SQL」「问题 + 权威结果 + 参考要点」，
   把「你觉得对吗」变成「对照批改（rubric + reference）」，否则退化成两个模型对喷。

**两处「确定性 → LLM」升级**（只在代码判不死时才动 LLM）

- **L1**：命中必需表但**多选了表** → 交 LLM 判多余表是否改变口径；
- **L4**：结果集严格比较不一致（多为形状/列数不同，如姓名被拆成两列）→ 交 LLM 判「口径是否等价」。

**`pass = None` 的语义**：裁判返回 `None`（如 JSON 解析失败 / LLM 关闭）表示「未给出结论」，
不计入通过性，但会在明细里显式暴露 —— **不掩盖问题**。

## 2.8 功能验收点汇总

| 功能 | 验收方式 | 判定信号 | 当前状态 |
|---|---|---|---|
| F1 自然语言问答 | 跑 `DEMO_QUESTIONS` + 12 题验证集 | 有表格/答案输出，无 error | ✅ |
| F2 追问改写 | 多轮 Demo（G6 后追问「那德国呢」） | 改写后问题不依赖上下文 | ✅ |
| F3 范围判定 | `N1/N2`（out）、`N3`（ambiguous） | L0 标签与权威一致 | ✅ 100%（12/12） |
| F4 选表 | 12 题 L1 | 覆盖必需表，多余表无害 | ✅ 100%（12/12） |
| F5 SQL 生成 | 12 题 L2 | 与权威 SQL 语义等价 | ⚠️ 91.7%（11/12，G7 口径分歧） |
| F6 只读守卫 | 自测四例（`SELECT`/`WITH`/`DROP`/多语句） | 前两者放行、后两者拦截 | ✅ |
| F8 改写回环 | 构造错误 SQL | 重试 ≤3 次后修正或诚实放弃 | ✅ |
| F11 渲染 | 结果集形状判定 | 1 类别 + 1 数值列自动出图 | ✅ |
| F13 逐层裁判 | 跑全量 12 题 | 输出明细表 + 汇总 + 失败定位 | ✅ |

> 说明：本节讲「怎么验」，[§2.1](#21-能力总览) 讲「有什么」，两张表职责不同，不重复。

---

# 三、技术设计文档

## 3.1 系统全景与组件关系

### 图 1：系统全景

**这张图在说什么**：从用户的一句话，到数据库上真实执行的一条 SQL，再到一张表 —— 全链路一屏看完。

```
┌──────────────────────────────────────────────────────────────────────────┐
│ 用户（业务方 / 分析师）                                                     │
│   「销量最高的 5 个流派是什么？」 「那德国呢？」 「明天天气怎么样？」          │
└────────────────────────────────┬─────────────────────────────────────────┘
                                 │ 自然语言
                                 ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ ChatBI.ask()  ── 三道闸门（决定「该不该答、答什么」）                        │
│   [A] rewrite_followup  追问 → 自包含问题                                  │
│   [B] scope_check       in / out / ambiguous（含结构性兜底）                │
│   [C] 分支              out→谢绝 | ambiguous→反问 | in→↓                   │
└────────────────────────────────┬─────────────────────────────────────────┘
                                 │ 自包含问题
                                 ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ run_workflow()  ── 7 阶段确定性流水线                                      │
│   [0]环境 [1]解析 [2]选表 [3]生成 [4]校验 ⇄ [5]改写(≤3) [6]执行 [7]汇总     │
└────────────────────────────────┬─────────────────────────────────────────┘
                                 │ SQL 文本（模型唯一产物）
                                 ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ is_readonly() 代码护栏 → check_sql() → execute_sql()                       │
│ SQLAlchemy Core(sqlite/pysqlite) → sqlite3 模块 → SQLite C 库 → chinook.db │
└────────────────────────────────┬─────────────────────────────────────────┘
                                 │ columns + rows
                                 ▼
              render_result()  表格 / 自动柱状图  →  自然语言答案
```

### 图 2：组件关系（谁调用谁）

**这张图在说什么**：结构类关系 —— 箭头表示「依赖 / 调用」，不表示顺序。
`chat()` 是唯一 LLM 出口；`JudgeAgent` 是**旁路**，不进生产链路。

```
                        生产链路
   ┌─────────────────────────────────────────────────────────────────────┐
   │                                                                     │
   │  ┌───────────────┐          ┌───────────────────┐                   │
   │  │ ChatBI.ask()  │─────────▶│  run_workflow()   │                   │
   │  │ ├ history[5]  │          │   [0] … [7] 编排   │                   │
   │  │ └ render_     │          └───┬───────┬───────┘                   │
   │  │    result()   │              │       │                           │
   │  └───────┬───────┘              │       │                           │
   │          │                      │       └──────────────┐            │
   │          │  闸门阶段调用          │ 阶段函数调用           │            │
   │          │                      ▼                     ▼            │
   │          │        ┌──────────────────────┐   ┌─────────────────┐   │
   │          │        │ select_tables        │   │  is_readonly()  │   │
   │          │        │ generate_sql         │   │  代码护栏        │   │
   │          │        │ rewrite_sql          │   │  （不调 LLM）    │   │
   │          │        │ summarize            │   └─────────────────┘   │
   │          │        └──────────┬───────────┘            │            │
   │          │                   │                        │            │
   │          │                   ▼                        │            │
   │          │           ┌──────────────┐                 │            │
   │          │           │   chat()     │────▶ LLM        │            │
   │          │           └──────────────┘   (DeepSeek)    │            │
   │          │                                            ▼            │
   │          │                          ┌──────────────────────────────┐│
   │          │                          │  DBParser                    ││
   │          │                          │   ├ table_names              ││
   │          │                          │   ├ get_table_fields()       ││
   │          │                          │   ├ get_data_relations()     ││
   │          │                          │   ├ get_table_sample()       ││
   │          │                          │   ├ check_sql()              ││
   │          │                          │   └ execute_sql() ───────────┼┼──▶ SQLAlchemy
   │          │                          └──────────────────────────────┘│   Core
   │          │                                                          │   → sqlite3
   │          └──▶ rewrite_followup() ──▶ chat()                          │   → chinook.db
   │               scope_check()      ──▶ chat()                          │
   └─────────────────────────────────────────────────────────────────────┘

                        评测链路（旁路，不写数据）
   GOLDEN_SET（12 题，冻结）
    ├── question / scope / tables ── 期望值
    └── ref_sql ──▶ freeze_golden() ──▶ ref_rows（双引擎固化）
                        │
   ChatBI.ask() 结果 ──▶ trace ──▶ ┌──────────────────────────────┐
   （或直接跑 Agent 链路）          │  JudgeAgent.judge(item, trace)│
                                   │   L0_scope / L1_tables        │
                                   │   L2_sql   / L3_guard         │
                                   │   L4_result / L5_answer       │
                                   └───────────────┬──────────────┘
                                                   ▼
                                       逐题逐层明细表 + 汇总指标
```

## 3.2 技术选型

| 维度 | 选择 | 理由 |
|---|---|---|
| LLM | DeepSeek（`deepseek-v4-flash`） | 走 OpenAI 兼容接口，成本低；**显式使用 Chat Completions**，关闭 tracing |
| 数据访问层 | **SQLAlchemy Core**（不是 ORM） | 见下文 |
| 数据库 | SQLite 单文件 `chinook.db` | 零配置、可离线复现；换库只需改 `DB_URL` |
| 交互 | ipywidgets | 不阻塞内核，`Run All` / `nbconvert` 可跑完 |
| 可视化 | matplotlib + pandas | 结果集形状满足条件时自动出柱状图 |
| 输出格式 | tabulate / DataFrame | 表格渲染 |

### 数据访问层：为什么用 Core 而不是 ORM

**先厘清一个概念**：SQLAlchemy 本身分两层，ORM 不是它的对立面，而是它自带的可选上层 ——
**Core** 是 SQL 工具箱（`Engine` / `text()` / 表达式语言 / schema 反射），
**ORM** 建在 Core 之上（把表映射成类、行映射成对象，**代你生成 SQL**）。
所以「ORM vs SQLAlchemy」严格说是「**ORM 层 vs Core 层**」。本项目用的是 **Core**。

| 维度 | ORM | Core（本项目） |
|---|---|---|
| 心智模型 | 领域对象（`class` ↔ 表） | 表 / SQL |
| 查询写法 | `session.query(Track).filter(...)` | `select(...)` 或 `text(sql)` |
| 结果形态 | 映射后的对象实例 | 裸 `Row` / `tuple` |
| 主键外键 | `relationship()` 对象导航 | `inspect()` 取元数据，自己 JOIN |
| schema 迁移 | 配合 Alembic 很顺 | 无内置迁移 |
| 抽象程度 | 高（SQL 被隐藏 / 生成） | 低（SQL 可见、可控） |

**本项目选 Core 的四条理由**

1. **产品本身就是一个字符串**：核心产物是「一条只读 SQL + 它的结果」，需要的是**可控地执行 SQL**，
   而不是「把行映射成对象」—— ORM 的看家本领在这里用不上。
2. **安全护栏依赖「可见的 SQL」**：拦写入的必须是代码结构。守卫直接吃文本 SQL 最直接；
   用 ORM 会把这条链藏进框架里，守卫点无处安放。
3. **schema 反射正好是 Core 的能力**：把表结构喂给 LLM 这一步，`inspect()` / `Table` 反射最省事；
   ORM 反而要先写死模型类。
4. **MVP / 不为不存在的需求设计**：只读问答不需要 CRUD 与领域模型，引入 ORM 只会增加
   会话状态（`Session` 身份映射、脏检查、事务边界）与隐式行为，收益为零。

> **反向说明**：如果这是个「订单 / 用户 / 库存」的**读写后台**，需要对象导航 + 迁移 + 复杂领域逻辑，
> 那 ORM 更合适。选型看场景 —— 不是 ORM 不好，是场景不同。

## 3.3 模块与接口契约

| 模块 | 方法 / 函数 | 签名要点 | 返回 |
|---|---|---|---|
| `DBParser` | `__init__(db_url)` | 建 engine + inspector | 实例 |
| | `get_table_fields(table_name)` | 字段清单 | `DataFrame` |
| | `get_data_relations()` | 外键关系 | `DataFrame` |
| | `get_table_sample(table_name)` | 样例行 | `DataFrame` |
| | `check_sql(sql)` | 试跑一次 | `(ok, err)` |
| | `execute_sql(sql)` | 真正执行 | `(columns, rows)` |
| 阶段函数 | `table_catalog(parser)` | 「表清单 + 字段」文本 | `str` |
| | `select_tables(question, parser)` | == 参考实现的 SQL Planner | `list[str]` |
| | `schema_context(tables, parser)` | 选中表的 schema 上下文 | `str` |
| | `generate_sql(question, tables, parser)` | 生成一条 SQL | `str` |
| | `rewrite_sql(question, sql, error, tables, parser)` | 失败改写 | `str` |
| | `summarize(question, sql, rows, max_rows=50)` | 结果汇总 | `str` |
| 编排 | `run_workflow(question, parser, verbose=True)` | 7 阶段串接 + 回环 | `trace` 字典 |
| 守卫 | `is_readonly(sql)` | 结构性只读校验 | `(bool, str)` |
| 交互 | `scope_check(question, parser)` | 范围判定 | `dict` |
| | `rewrite_followup(question, history)` | 追问改写 | `str` |
| | `render_result(res, max_rows=None)` | 渲染表格/图表 | `None`（副作用显示） |
| | `ChatBI(parser, max_history=5)` / `.ask(question, show_trace=False)` | 多轮入口 | `trace` 字典 |
| 造题 | `simulate_question` / `simulate_questions` / `simulate_out_of_scope` | 造题 | `dict` / `list` |
| 裁判 | `freeze_golden(golden=None, parser=None)` | 双引擎固化 `ref_rows` | `GOLDEN_SET` |
| | `judge_scope` / `judge_tables` / `judge_sql` / `judge_guard` / `judge_result` / `judge_answer` | L0–L5 单层裁判 | `dict` |
| | `JudgeAgent(use_llm=True)` / `.judge(item, trace)` | 逐层聚合 | `dict` |

**`run_workflow` 的 `trace` 契约**（关键字段）

```python
trace = {
    "question": ..., "tables": None, "sql": None,
    "attempts": 0,        # 校验/改写轮次
    "ok": False,          # 是否产出可执行 SQL
    "error": None,        # 失败原因（ok=False 时非空）
    "columns": None, "rows": None,
    "answer": None,       # 失败时为"抱歉，未能生成可执行的 SQL，已放弃（不编造结果）。"
}
```

**`ChatBI.ask` 在 `trace` 上追加**：`raw_question` / `standalone_question` / `scope`。

## 3.4 数据访问层与执行链路

**这张图在说什么**：模型全程不碰数据库 —— 它唯一的产物是一段**文本**，之后每一步都是代码。

```
 LLM 输出一个字符串（就是 SQL 文本）        ← 模型唯一的产物，纯文本
        │
        ▼
 is_readonly(sql)                          ← 代码护栏
   ├─ 去行注释、去块注释、去尾分号
   ├─ 含 ";" → (False, "不允许多条语句")
   ├─ 首词非 SELECT|WITH → (False, "只允许 SELECT / WITH 只读查询")
   ├─ 剔除字符串字面量后扫描写关键字 → (False, "检测到非只读关键字：xxx")
   └─ 通过 → (True, "ok")
        │ ok
        ▼
 parser.check_sql(sql)                     ← 试跑一次，确认语法 / 语义可执行
        │
        ▼
 parser.execute_sql(sql)                   ← 真正执行
   conn.execute(text(sql)) → [tuple(r) for r in result.fetchall()]
        │
        ▼
 SQLAlchemy Core（dialect: sqlite/pysqlite）
        │
        ▼
 sqlite3 模块 → SQLite C 库 → 读 chinook.db 文件
        │
        ▼
 columns + rows → pd.DataFrame
```

**唯一换库入口**：`DB_URL`。反射层（`create_engine` / `inspect`）与流水线编排跨库通用，
换库主要动「连接串 + 提示词方言 + 守卫复核」三处。

**实跑证据（详见 [§3.7](#37-验证与评测) 与 README「结构性取证」）**

| # | 验证项 | 结果 |
|---|---|---|
| ① | 引擎 | `sqlite:///…/chinook.db`，dialect `sqlite/pysqlite` |
| ② | `execute_sql("SELECT COUNT(*) AS tracks FROM tracks")` | `columns=['tracks']`，`rows=[(3503,)]` |
| ③ | 原生 `sqlite3` 直连同一文件交叉验证 | `[(3503,)]`，与 ② 一致 |
| ④ | `is_readonly()` | `SELECT` / `WITH` 放行；`DELETE` / `DROP` / `UPDATE` / 多语句 **拦截** |
| ⑤ | 在副本上改数据 | genres `25 → 26`，原始库保持 `25`（证明读的是文件当前内容） |
| ⑥ | 直连副本执行 `DELETE` | `rowcount=1`（说明裸执行器本身不拦写 → 护栏必须在编排层/执行器内） |

## 3.5 提示词与防御式设计

**Structure vs Prompt**（本项目的核心主张）

| | Prompt 约束（概率性） | 本实现 Structure（确定性） |
|---|---|---|
| 做法 | 提示词里写「只准写 SELECT」 | `is_readonly()` 正则白名单：首词必须 `SELECT`/`WITH`、去注释与字符串字面量后再扫关键字、禁多语句 |
| 失败时 | 模型可能不听 | 返回 `(False, 原因)` → `run_workflow` **直接 `return`**，根本不调用 `execute_sql` |
| 结果 | 「希望」不写坏语句 | 写操作**在到达数据库之前就被代码挡掉**，与模型意愿无关 |

**确定性优先的两个取舍**

1. **一次性灌 schema，而不是让 LLM 调工具取表**：参考实现用 `function_tool` 让选表 Agent
   按需取字段；本实现改为选表后**一次性**把 schema 灌进提示词 —— 功效等价、更确定、少一轮工具往返。
2. **分支逻辑写在 Python 层，不写在提示词里**：`scope` 的三分支、改写回环的次数、
   `in` 的结构兜底降级，全部由代码决定。

## 3.6 安全与脱敏

**三条红线**

1. **凭据绝不进代码 / notebook** —— 一律 `.env` + `.gitignore`；
   notebook 用 `find_up(".env")` 从 CWD 逐级向上找，源码中不含明文 Key。
2. **只读双保险** —— 代码守卫（`is_readonly`）+ 数据库侧只读账号；两者独立，任一失效还有另一道。
3. **大库别全量灌 schema** —— 否则提示词超长、token 爆炸、初始化拖死。

**已落地的脱敏措施**

| 措施 | 说明 |
|---|---|
| `.env` 定位 | `find_up(".env")`，不在源码硬编码路径 |
| Key 伪装 | `OPENAI_API_KEY = DEEPSEEK_API_KEY`，源码中不出现真实 Key |
| 路径脱敏 | `_redact(p)` 把本机绝对路径替换为 `~` 形式后再展示 |
| 字体兜底 | 预配 `Arial Unicode MS` 等中文字体，并 `catch_warnings` 屏蔽含绝对路径的渲染警告 |

**已知的加固点（暂未实施）**

1. 把 `is_readonly` 也放进 `execute_sql()` 内部 —— 现在是「编排层不调它」，
   改成「它自己也不执行写」，加第二道锁（对应实跑证据 ⑥）。
2. `check_sql()` 用 `engine.begin()`（自动提交）试跑 —— 当前顺序是先 `is_readonly` 再 `check_sql`
   所以安全；但顺序一旦被改动即有风险，可改为只读事务（`BEGIN` + `ROLLBACK`）试跑。

## 3.7 验证与评测

### 验证集冻结机制

- `GOLDEN_SET` 一次编好、**只读**；
- `ref_rows` 由手写 `ref_sql` 经**双引擎（SQLAlchemy + 原生 `sqlite3`）交叉验证**后固化；
- 评测时只读、**绝不重算** —— 既保证可复现，也让「答案」独立于「答题模型」，避免自证偏差；
- `freeze_golden()` 可随时重建（不一致立即 `AssertionError`）。

```python
def freeze_golden(golden=None, parser=None):
    """重建并固化 ref_rows：双引擎交叉验证，不一致立即报错。"""
    import sqlite3
    golden = golden if golden is not None else GOLDEN_SET
    con = sqlite3.connect(str(_db_path))
    try:
        for it in golden:
            if it["scope"] != "in":
                continue
            _, rows_a = parser.execute_sql(it["ref_sql"])    # 引擎 A：SQLAlchemy
            rows_b = con.execute(it["ref_sql"]).fetchall()   # 引擎 B：原生 sqlite3
            if sorted(map(str, rows_a)) != sorted(map(str, rows_b)):
                raise AssertionError(f"{it['id']} 双引擎结果不一致：{rows_a} vs {rows_b}")
            it["ref_rows"] = rows_a
    finally:
        con.close()
    return golden
```

### 逐题逐层明细（实测，`deepseek-v4-flash` 一次跑全量）

| 题号 | 问题 | scope | L0 | L1 | L2 | L3 | L4 | L5 | 权威答案 `ref_rows` | 标签 |
|---|---|---|---|---|---|---|---|---|---|---|
| G1 | 数据库里一共有多少首歌曲？ | in | PASS | PASS | PASS | PASS | PASS | PASS | `3503` | count |
| G2 | 数据库里一共有多少张专辑？ | in | PASS | PASS | PASS | PASS | PASS | PASS | `347` | count |
| G3 | 数据库里一共有多少位客户？ | in | PASS | PASS | PASS | PASS | PASS | PASS | `59` | count |
| G4 | 数据库里一共有多少名员工？ | in | PASS | PASS | PASS | PASS | PASS | PASS | `8` | count |
| G5 | 账单国家是德国的发票有多少张？ | in | PASS | PASS | PASS | PASS | PASS | PASS | `28` | count, filter |
| G6 | 销量最高的 5 个流派分别是什么，各卖了多少？ | in | PASS | PASS | PASS | PASS | PASS | PASS | Rock 835 / Latin 386 / Metal 264 / Alternative & Punk 244 / Jazz 80 | join, aggregate, topk |
| G7 | 每位客服各自负责多少位客户？ | in | PASS | PASS | **FAIL** | PASS | PASS | PASS | Jane Peacock 21 / Margaret Park 20 / Steve Johnson 18 | join, group |
| G8 | 哪个客户的累计消费金额最高？ | in | PASS | PASS | PASS | PASS | PASS | PASS | Helena Holý 49.62 | join, topk |
| G9 | 2010 年的总销售额是多少？ | in | PASS | PASS | PASS | PASS | PASS | PASS | `481.45` | filter, aggregate |
| N1 | 明天北京的天气怎么样？ | out | PASS | PASS | PASS | PASS | PASS | PASS | ——（闸门样本） | gate |
| N2 | 帮我写一首关于夏天的诗 | out | PASS | PASS | PASS | PASS | PASS | PASS | ——（闸门样本） | gate |
| N3 | 有多少？ | ambiguous | PASS | PASS | PASS | PASS | PASS | PASS | ——（闸门样本） | gate |

### 逐层汇总指标

| 层 | 通过率 | 判定题数 |
|---|---|---|
| L0_scope | 100.0% | 12 |
| L1_tables | 100.0% | 12 |
| L2_sql | **91.7%** | 12 |
| L3_guard | 100.0% | 12 |
| L4_result | 100.0% | 12 |
| L5_answer | 100.0% | 12 |
| **端到端** | **91.7%** | **11/12** |
| 可疑数字 | 0 个 | （诊断信号，不参与通过性） |

### 失败定位

```
- [G7] 每位客服各自负责多少位客户？  失败层：['L2_sql']
```

**G7 复盘**：Agent 用 `LEFT JOIN` + `Title` 过滤，权威 `ref_sql` 用 `INNER JOIN` ——
两者**结果恰好相同**（`pass` 为 True），差异只在 SQL 的**口径**上，属于「口径分歧」而非「答错」。
这正是「逐层」的价值：**定位到具体一跳并给出可读理由**，而不是只报一个「对/错」；
至于谁的口径更对，仍需人工判定。

### 三处裁判缺陷的修复记录（装置演进）

| 缺陷 | 现象 | 修复 |
|---|---|---|
| L0 全线 FAIL | `in` 题直调 `run_workflow` 绕过闸门，`trace` 里没有 `scope` | 统一走 `ChatBI.ask()`，让所有题目都经过闸门 |
| L4 对 G7/G8 误判 | Agent 把姓名拆成 `FirstName`/`LastName` 两列，严格等集比较是「充分不必要条件」 | 新增 `judge_result_llm`：判不一致时升级 LLM 做「口径等价性」复核 |
| 可疑数字 5 处误报 | 「前 5 名」「2.2 倍」等派生数字被当成溯源失败 | 加「派生合计豁免 + 名次/编号豁免」 |

修复后：L0 `25% → 100%`、L4 `83.3% → 100%`、端到端 `3/12 → 11/12`、可疑数字 `5 → 0`。

## 3.8 Notebook cell 索引

`sql-agent-workflow.ipynb` 共 **43 个 cell（索引 0–42）**。

| Notebook 章节 | cell 范围 |
|---|---|
| 标题 + 与参考实现角色对照 | 0–1 |
| §0 环境准备（脱敏加载 `.env`） | 2–3 |
| §1 数据库解析（`DBParser`） | 4–8 |
| §2 只读安全守卫（`is_readonly`） | 9–10 |
| §3 LLM 封装（DeepSeek · Chat Completions） | 11–12 |
| §4 流水线各阶段函数（Stage 2/3/5/7） | 13–14 |
| §5 编排：7 阶段串接 + 校验/改写闭环 | 15–16 |
| §6 Demo：示例问题 + 手动问答入口 | 17–20 |
| §7 问题模拟生成器 | 21–23 |
| §8 小型评测（`tqdm` + 真值校验） | 24–28 |
| §9 范围判定与用户对接（`ChatBI` + ipywidgets） | 29–34 |
| §10 逐层裁判验证（冻结验证集 / 裁判 / 跑全量） | 35–41 |
| §11 结论与已知限制 | 42 |

## 3.9 快速开始（复现）

```bash
# 1) 装依赖（本项目新增 sqlalchemy、tabulate；其余 pandas / matplotlib / ipywidgets 等已具备）
pip install sqlalchemy tabulate

# 2) 在项目根目录放 .env（变量名见 .env.example，值需自行填写，切勿提交）
#    DEEPSEEK_API_KEY=...
#    DEEPSEEK_BASE_URL=...
#    DEEPSEEK_MODEL=deepseek-v4-flash

# 3) 打开并运行 notebook，或命令行无头执行
jupyter nbconvert --to notebook --execute --inplace sql-agent-workflow.ipynb
```

**前置条件**：`chinook.db` 与 notebook 同目录（`find_up("chinook.db")` 从 CWD 逐级向上查找）；
`.env` 放在 notebook 所在目录或其任意上级目录。

**跑完你会看到**：§6 示例问题输出与会话轨迹；§8 小型评测指标；§9 ChatBI 多轮 + 交互输入框；
§10 逐题逐层明细表 + 汇总指标 + 失败定位。

## 3.10 仓库结构与交付物

本目录实际文件（7 个）：

| 文件 | 大小 / 形态 | 是否发布 | 说明 |
|---|---|---|---|
| `sql-agent-workflow.ipynb` | 43 cells | ✅ | **主交付物**，全程无头可执行 |
| `README.md` | 约 487 行 | ✅ | 架构 / 设计点 / 选型 / 数据来源 / 已知限制 |
| `外部数据库接入指南.md` | 约 163 行 | ✅ | Gate 0–5、方言对照、建只读账号、冒烟自检 |
| `ChatBI-Design-Doc.md` | 本文档 | ✅ | 项目 / 功能 / 技术三份设计 |
| `.env.example` | 约 306 B | ✅ | 变量模板，**不含真实密钥** |
| `chinook.db` | 约 864 KB（884736 B） | ✅ | 数据文件；SQLite 3.x，Chinook 示例库 |
| `sql-agent.ipynb` / `code-agent.ipynb` | 参考实现 | ❌ **未发布** | ⚠️ **含明文 API Key**，已在发布流程中排除 |

> **安全提醒**：`sql-agent.ipynb` 与 `code-agent.ipynb` 是**内部参考实现**，其中硬编码了明文 API Key。
> 这两个文件**从未对外发布**，但 Key 本身已在本地文件中存在过 ——
> **建议尽快在服务商侧吊销旧 Key**，并把这两个 notebook 改为读取 `.env`（复用本项目的 `find_up(".env")` 机制），
> 以免日后误提交。这正是三条红线第一条要防的场景。

**已发布地址**：`https://github.com/PhilipGuan/hub-LsAp/tree/main/guanbing/week11/ChatBI-SQL-based`

## 3.11 常见改动指引（How-To）

| 想改什么 | 改哪里 | 影响面 | 验收 |
|---|---|---|---|
| **换模型** | `.env` 的 `DEEPSEEK_MODEL` | 无代码改动；注意模型能力差异会影响 SQL 质量 | 跑 12 题验证集，逐层指标不低于基线 |
| **换数据源（同 SQLite）** | 环境准备 cell 的 `find_up("*.db")` 文件名 | 仅定位逻辑 | 能反射新库 + 跑通一条聚合查询 |
| **接外部真实库** | `DB_URL` + 装驱动 + 数据库侧建只读账号 | 守卫需复核（方言相关）、`check_sql` 建议改只读事务 | 见《外部数据库接入指南》Gate 2–3 验收 |
| **扩验证集** | 往 `GOLDEN_SET` 加「问题 + 手写 `ref_sql`」 | 无 | 跑 `freeze_golden()` 无 `AssertionError`，再跑全量 |
| **加交互外壳** | 把 `ChatBI.ask()` 抽为后端函数，外套 Streamlit / FastAPI | 需处理会话历史持久化 | 前端能提问并拿到表格/图表 |
| **调绘图触发条件** | `render_result()` 里的列型判断与 `chart_max` | 无 | 1 类别 + 1 数值列时出图、柱子过多时不出图 |
| **加固只读** | 把 `is_readonly` 移入 `execute_sql()`；`check_sql` 改只读事务 | 影响所有执行路径 | 实跑证据 ⑥ 的场景被拦下 |

## 3.12 演进规划（Gate 0–5）

**这张图在说什么**：从本地 SQLite 到外部真实业务库的 6 级阶梯，每一步都可独立验收。
原则是 **先跑通，再优化**，不一次到位。

```
 Gate 0 ── 现状：SQLite 本地文件，零配置
    │
    │  改「文件名」
    ▼
 Gate 1 ── 换个 SQLite 文件
    │      验收：能反射新库 + 整条流水线跑通一条聚合查询
    │
    │  改「DB_URL」+ 装驱动 + 数据库侧建只读账号
    ▼
 Gate 2 ── 接入外部库（MySQL / PostgreSQL / SQL Server）
    │      验收：反射 + 聚合查询正确；凭据只进 .env，绝不打印
    │
    │  改「提示词方言」+ 复核「只读守卫」
    ▼
 Gate 3 ── 适配方言与守卫
    │      验收：小评测集（复用 GOLDEN_SET 思路）在目标库通过
    │
    │  统计改惰性 + schema 按问题检索候选表
    ▼
 Gate 4 ── 大库可扩展性
    │      验收：启动时间可接受、提示词长度受控
    │
    │  连接池参数 / 审计日志 / 凭据轮换
    ▼
 Gate 5 ── 产品化
           验收：只读强约束落到 CI / 演练，确认写操作真的进不来
```

**各 Gate 要点**

| Gate | 关键动作 | 风险 |
|---|---|---|
| 0 现状 | 作为基线 | —— |
| 1 换 SQLite 文件 | 不涉驱动 / 网络 / 凭据，成本最低 | —— |
| 2 接外部库 | **第一道护栏放在数据库本身**（只读账号），代码守卫只是第二道 | 网络可达 / 超时 / **连接串泄漏** |
| 3 适配方言 | 提示词从「SQLite 专家」改成目标方言；守卫回归复核（`SHOW`/`EXPLAIN`/`CALL` 等） | 「本地能跑、线上报错」 |
| 4 大库 | 全库预统计 → 惰性统计；全量灌 schema → 按问题检索候选表 | 初始化查询风暴 |
| 5 产品化 | 连接池 / 超时重试 / 审计日志 / 密钥管理 | 长期静态口令 |

> 完整可执行版（分阶段清单、方言对照表、建只读账号 SQL、换库冒烟脚本）见同目录
> [`外部数据库接入指南.md`](外部数据库接入指南.md)。

## 3.13 已知限制与技术债

**A. 概率性（LLM 固有）**

1. LLM 生成 SQL 属**概率性**，同一问题两次可能得到不同 SQL；靠「只读 + 校验闭环」兜底，
   但不保证 100% 正确。
2. ChatBI 的**追问改写**是概率性判断：边界模糊的追问可能改写不符预期；
   用「[A] 先改写 → [B] 再判定」的顺序缓解，但不保证 100%。
3. **范围判定**（`scope_check()`）同为概率性：用「结构兜底」（`in` 的 `tables` 必须落库，
   否则降级 `ambiguous`）降低误判，但不保证 100%。
4. Table 选择为**单轮判断**，跨多表复杂 join 时可能漏表；已在表摘要中喂入外键关系缓解。

**B. 验证装置的天花板**

5. **验证集规模小**（12 题，覆盖 count / filter / join / group / topk / 闸门），
   要当硬门槛还需扩样；扩充只需往 `GOLDEN_SET` 加题再跑 `freeze_golden()`。
6. **L2 / L5 是 LLM 裁判**，本身也具概率性：已用「带参照物 + 只输出 JSON + 解析失败记 `None`」
   约束，但不保证 100% 判准。
7. **L4 引入 LLM 复核后不再是纯确定性判据**：形状不同时改由 LLM 判「口径是否一致」，
   结果从「字节相等」变为「语义等价」。
8. **数字溯源只是诊断信号，不参与通过性**：已做「派生合计豁免 + 名次/编号豁免」抑制误报，
   仍有残余噪音。
9. **未通过 ≠ 一定错**：L2 的 FAIL 可能只是口径分歧（如 G7），
   装置只负责定位到具体一跳并给出理由，最终谁对仍需人工判定。
10. **权威口径由人写 `ref_sql` 定义**：若口径本身有争议，裁判结论也随之有争议 ——
    这是验证集的天花板。

**C. 结构性 / 工程债**

11. `execute_sql()` 本身是**裸执行器**（不校验只读），护栏只在 `run_workflow` 的 `is_readonly` 一步；
    若有代码绕过编排直接调用它，写语句会被真的执行（加固方案见 [§3.6](#36-安全与脱敏)）。
12. `check_sql()` 用 `engine.begin()`（自动提交）试跑 —— 顺序一旦被改动即有风险。
13. `scope_check()` 与 `select_tables()` 有一次**重复的选表调用**（都基于同一 catalog），
    可合并以省一次 LLM 调用。
14. 澄清反问为**单轮**：`ambiguous` 分支给出澄清语后需用户下一条消息继续；
    未做「反问 → 等待 → 续接」的闭环状态机（Non-Goal N3）。
15. `DBParser` 初始化会**逐表逐字段跑统计**（distinct / 众数 / max / min / 随机样例）——
    小库无感，真实大库会变成「初始化查询风暴」（Gate 4 待解决）。
16. 评测集由 LLM 合成，存在**自证偏差**（同类模型造题又答题）；
    指标只用于横向对比流程是否有效，**不等同于真实业务准确率**。

---

> **相关文档**：同目录 [`README.md`](README.md)（架构 / 选型 / 数据来源）、
> [`外部数据库接入指南.md`](外部数据库接入指南.md)（Gate 0–5 / 方言对照 / 冒烟自检）。
>
> **数据口径**：本文所有实测数字（`tracks=3503`、12 题逐层结果、L2 91.7% 等）取自
> `sql-agent-workflow.ipynb` 的**实跑输出**（模型 `deepseek-v4-flash`），未做二次推算。
