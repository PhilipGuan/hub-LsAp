# SQL Agent · LangChain 版（`07-sql-agent-langchain`）

把 [`04_SQL-Code-Agent-Demo/sql-agent-workflow.ipynb`](../04_SQL-Code-Agent-Demo/sql-agent-workflow.ipynb)
（裸 OpenAI SDK + 手写编排）**用 LangChain / LangGraph 重写**后的工程化版本。

> **原目录一字未改。** 本目录是重写后的新交付物，两者可并排对照。
>
> 一句话结论：**换框架 ≠ 换行为**。提示词、重试次数、`temperature=0`、「失败即放弃、不编造」的语义
> 全部保留；差异只在「这些行为由框架的哪个部件提供」。

---

## 1. 为什么用 LangChain 重写是合理需求

原实现能跑、结论也对，重写的价值不在「功能更多」，而在**把散落在 notebook cell 里的编排收敛成可复用的工程结构**：

| 原实现的痛点 | 重写后 |
|---|---|
| 编排写在 `for` / `if` 里，拓扑只能靠读代码脑补 | LangGraph `StateGraph` 显式声明，`graph.get_graph().draw_mermaid()` 可画、可核对 |
| `json.loads` + `.get()` 手工兜底，结构靠约定 | Pydantic 结构化输出，字段类型由框架校验 |
| 手写 `for _ in range(3): try/except` 重试 | `model.with_retry(stop_after_attempt=3)`，框架内建 |
| 模型接入写死 OpenAI SDK 细节，Key 要伪装进 `os.environ` | `ChatOpenAI` 显式传参，**不再污染全局环境变量**，换 provider 只改一处 |
| 只能「确定性流水线」一种风格 | 同内核并列两条路：`workflow/`（确定性编排）+ `agents/`（自主编排） |
| 评测靠肉眼 | `judge/` + 冻结验证集，L0–L5 逐层定位失败到具体一跳 |

**同时坚持「不是所有东西都该 LangChain 化」**：只读守卫 `is_readonly()`、`DBParser` 反射、
L0–L4 确定性判分**仍是纯 Python**——护栏做进**代码结构**，而不是提示词。详见 [DESIGN.md](DESIGN.md)。

---

## 2. 两条并列路径（共用同一套内核）

```
用户问题
   │
   ▼  ── ChatBI：对接用户的三道闸门（两条路共用）──
[A] 追问改写  rewrite_followup()
[B] 范围判定  scope_check()   → in / out / ambiguous
[C] 分支      out → 谢绝；ambiguous → 反问；in → 进入下面的路
   │
   ├─── 路线 A：workflow/graph.py ── LangGraph 显式状态图（确定性编排）
   │      START → select → generate → check ─┬─ ok ────→ execute → summarize → END
   │                                          ├─ 未过 ─→ rewrite ─┐(回环)
   │                                          └─ 用尽 ─→ fail → END
   │
   └─── 路线 B：agents/sql_agent.py ── create_agent（把决策权交给模型）
          三角色（planner→generator→守卫/改写→summarizer）
          或 单 Agent + 工具（list_tables / parse_table / run_sql，模型自决顺序）
```

**关键认知**：LangChain v1 的 `create_agent` **内部就是用 LangGraph 实现的**。
所以二者不是二选一——一个管「每一步怎么调模型」，一个管「步骤之间怎么流转」。
本项目两条路都留着，正是为了把这个区别**跑给你看**。

---

## 3. 目录结构

```
07-sql-agent-langchain/
├── README.md / MIGRATION.md / DESIGN.md
├── requirements.txt / .env.example / .gitignore / chinook.db / pytest.ini
├── sql-agent-langchain.ipynb        ← 演示 notebook（已执行并保存输出）
├── sqlagent/                        ← 包本体（可 import，可 CLI）
│   ├── __init__.py                  ← PEP 562 懒导出（import 不牵出 matplotlib）
│   ├── config.py  db.py  guard.py   ← 配置 / 反射 / 只读守卫（纯 Python，未 LangChain 化）
│   ├── llm.py                       ← 模型接入层：ChatOpenAI · with_retry · with_structured_output
│   ├── schemas.py                   ← Pydantic 结构化输出契约
│   ├── prompts.py                   ← ChatPromptTemplate（提示词正文逐字保留）
│   ├── tools.py                     ← 两条路的唯一入口：schema 拼装 + @tool
│   ├── chatbi.py                    ← 三道闸门 + 多轮对话 + 结果渲染
│   ├── cli.py                       ← 命令行入口
│   ├── evalset.py                   ← 冻结验证集 GOLDEN_SET + 结构探针 STRUCTURE_PROBES + 造题器
│   ├── workflow/graph.py            ← 路线 A：LangGraph 状态图
│   ├── agents/sql_agent.py          ← 路线 B：create_agent
│   └── judge/                       ← L0–L5 逐层裁判（deterministic.py + llm.py）
└── tests/                           ← 离线单测（52 项，零网络）
```

---

## 4. 原实现 → LangChain 对照表

| 原实现（裸 SDK / notebook） | 本实现（LangChain） | 位置 |
|---|---|---|
| `OpenAI(api_key=..., base_url=...)` | `ChatOpenAI(model=..., api_key=..., base_url=...)` | [llm.py](sqlagent/llm.py) |
| 手写 `for _ in range(3): try/except` | `model.with_retry(stop_after_attempt=3)` | [llm.py](sqlagent/llm.py) |
| `messages=[{"role": "system", ...}]` 手工拼字典 | `ChatPromptTemplate.from_messages()` + 消息对象 | [prompts.py](sqlagent/prompts.py) |
| `response_format={"type":"json_object"}` + `json.loads` + 手工兜底 | `with_structured_output(Pydantic)` 直接拿校验过的对象 | [llm.py](sqlagent/llm.py) |
| `run_workflow()` 手写 `for` + `if` 编排 | LangGraph `StateGraph` / 节点 / 条件边 / 回环 | [workflow/graph.py](sqlagent/workflow/graph.py) |
| 参考实现的 `Agent` + `Runner` + `@function_tool` | `create_agent(...)` + `@tool` + `agent.invoke(...)` | [agents/sql_agent.py](sqlagent/agents/sql_agent.py) |
| 提示词写死在 cell 里 | 提示词正文**逐字保留**，改为 `ChatPromptTemplate` | [prompts.py](sqlagent/prompts.py) |

### 一个必须知道的坑（已在代码里注释说明）

`model.with_retry()` 返回的是 `RunnableRetry`，它**没有** `with_structured_output()` /
`bind_tools()` 这些**模型级**能力。所以正确顺序是：

```python
# 先用「原生模型」拿到能力，再套重试
structured = build_raw_model(attempts=0) \
    .with_structured_output(schema, method="json_mode") \
    .with_retry(stop_after_attempt=3)
```

见 [llm.py](sqlagent/llm.py) 的 `build_raw_model()` / `invoke_structured_messages()`。
这个坑是在**真实 DeepSeek 冒烟**时才暴露的——离线单测（假模型）测不出来。

---

## 5. 逐层裁判验证（JudgeAgent + 冻结验证集）

规模化使用前，先用「带答案的验证集」**逐层**校验取数逻辑。一次问答的错误可能注入在 5 个跳跃点，
只看终点值既无法定位，也会放过「结论对、过程错」（选错表却恰好数值撞对）：

```
问题 ─①→ 选表 ─②→ 生成 SQL ─③→ 校验 ─④→ 执行 ─⑤→ 汇总回答
```

**验证集冻结**：`GOLDEN_SET` 一次编好、只读；`ref_rows` 由手写 `ref_sql` 经
**双引擎（SQLAlchemy + 原生 `sqlite3`）交叉验证**后固化（`freeze_golden()`），评测只读、绝不重算。

**六层裁判（L0–L5）**

| 层 | 阶段 | 判据 | 类型 |
|---|---|---|---|
| L0 | 闸门 | scope 标签与权威一致 | 确定性 |
| L1 | 选表 | 覆盖必需表；多余表交 LLM 判是否无害 | 确定 + LLM |
| L2 | 生成 | 与权威 SQL 是否语义等价 | LLM |
| L3 | 校验 | 是否产出可执行 SQL（守卫放行） | 确定性 |
| L4 | 执行 | 结果集等价（行序无关 + 数值容差）；不一致时交 LLM 复核 | 确定 + LLM |
| L5 | 汇总 | 要点覆盖 + 数字能否溯源 | LLM + 确定性 |

> 端到端通过 = 各层「有判定」的项全部通过；裁判返回 `None` 表示「未给出结论」，不计入通过性，
> 但会在明细里显式暴露，不掩盖问题。

### 实测结果（`deepseek-v4-flash`，12 题一次跑全量）

| 层 | 通过率 | 判定题数 |
|---|---|---|
| L0 闸门 | 100.0% | 12 |
| L1 选表 | 100.0% | 12 |
| L2 生成 | 91.7% | 12 |
| L3 校验 | 100.0% | 12 |
| L4 执行 | 100.0% | 12 |
| L5 汇总 | 100.0% | 12 |
| **端到端** | **11/12** | 12 |

验证集 12 题（`in` 9 / `out` 2 / `ambiguous` 1），覆盖 count / filter / join / group / topk 与三道闸门。

**唯一未通过项 = G7 的 L2 口径分歧，且这是个「好失败」**：

- 问题：`每位客服各自负责多少位客户？`
- Agent 产出：`LEFT JOIN` + `WHERE e.Title = 'Sales Support Agent'`，保留 0 客户、按 Title 过滤；
- 权威 `ref_sql`：`INNER JOIN`，不按 Title 过滤，按客户数降序。
- **L4 结果层判 PASS**（21/20/18 完全一致，只是列拆分/增减），**L2 生成层判 FAIL**（连接关系与过滤条件不同）。

这正是「逐层」的价值：**定位到具体一跳并给出可读理由**，而不是只报一个「对/错」。
`L2 口径分歧 ≠ 结果错`——最终谁对仍需人工判定。

> **换框架的行为等价性在此得到验证**：原 `04` 项目 README 记录的评测结论同样是
> 「L2 91.7% / 端到端 11/12，唯一失败项是 G7 的 L2 口径分歧」。本实现**逐字复现**了该结论。

### 结构探针：5 道新题探查 L2 的边界

上面 12 题里 9 道 in 题有 **4 道是单表 `COUNT(*)`** —— 生成的 SQL 与权威几乎逐字相同，
**L2 轻轻松松就过了**。于是另立一组**结构探针** `STRUCTURE_PROBES`（5 题），
专门覆盖验证集没有的 5 种 SQL 结构：

| ID | 问题 | 探针结构 |
|---|---|---|
| P1 | 曲目数量超过 100 首的流派有哪些，分别有多少首？ | `HAVING` |
| P2 | 哪些曲目的时长比全库平均时长还长？请列出最长的 5 首。 | 标量子查询 |
| P3 | 每位员工的直属上司分别是谁？ | 自连接（含 `NULL`） |
| P4 | 按年份统计每年的销售总额，从早到晚列出。 | 日期分桶 `strftime` |
| P5 | 销售额最高的 3 位客户分别是谁，各排第几？ | CTE + 窗口函数 |

**先分清两个判据**（这是理解本节的关键）：

- **写法等价（L2 用）**：**不看数据**，只从两条 SQL 的**写法 / 口径**判断是否一致 ——
  过滤条件、连接关系、聚合口径、排序（有无 `ORDER BY`、按什么排）、取值列（如有没有 `ROUND`）。
  别名、列顺序这类**表层差异不算**。
- **结果等价（L4 用）**：**看跑出来的数据** —— 行集合是否相同、每行取值是否相同。

**实测（`deepseek-v4-flash`）**：L0 / L1 / L3 **全 PASS**、**L4 结果等价 5/5 通过**、
且 `attempts=1`（五种结构全部一次生成成功）；而 **L2 亮灯 3/5**。

| ID | L2（写法等价） | L4（结果等价） |
|---|---|---|
| P1 | FAIL · 缺 `ORDER BY` | PASS · 结果集严格一致 |
| P2 | PASS | PASS |
| P3 | PASS | PASS |
| P4 | FAIL · 少了 `ROUND(...,2)` | PASS · 结果集严格一致 |
| P5 | FAIL · `RANK` vs `ROW_NUMBER`、并列处理、`LIMIT` vs `WHERE rn<=3` | PASS |

**「L2 亮灯、L4 通过」不是矛盾，正是逐层裁判要交付的信息**：L2 是**审查信号**（写法变了，
值得看一眼），L4 是**判决**（数据对不对）。逐条看 L2 给的理由，可分成三类：

- **P4 是明显假阴性**：唯一理由「权威用了 `ROUND(...,2)`，待审没四舍五入」——而 L4 自己写的是
  「结果集严格一致」。纯化妆性差异。
- **P1 是口径过严**：唯一理由「缺 `ORDER BY`」，但问题本身没要求排序，L4 也是「结果集严格一致」。
- **P5 算可辩护的真差异**：裁判列出 `RANK` 的并列语义、第二排序键、`LIMIT 3` vs `WHERE rn<=3`
  等多条 —— **结果对，但写法确实不同**。

**还有一个必须承认的事实：LLM 裁判有 run-to-run 抖动。** 同一份代码连跑两次，
L2 亮灯数在 3/5 与 4/5 之间摆动（P1 一次 PASS 一次 FAIL）；`temperature=0` 也不保证可复现。

> 所以这 5 题**不并入 `GOLDEN_SET` 的通过性统计**（那会把「口径分歧」误记成「答错」），
> 而是作为**探针**常驻，专门持续监视 L2 的稳健性。要把它当硬门槛，得先决定 L2 的口径
> （坚持「写法等价」还是放宽到「结果等价」）。本实现**保留 L2 的字面口径不改**——
> 因为它的价值恰恰是当那个「值得看一眼」的信号。

---

## 6. 运行方式

### 6.1 环境

仓库根目录的 `.venv`（python 3.12）已具备全部依赖，通常**无需新建环境**。
如需自包含安装：

```bash
pip install -r requirements.txt
```

### 6.2 配置

从仓库根目录的 `.env` 读取（`sqlagent/config.py` 会逐级向上查找 `.env`），本目录**不放真实密钥**：

```dotenv
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-你的_KEY
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEEPSEEK_MODEL=deepseek-v4-flash
```

### 6.3 三种跑法

```bash
# ① 命令行（workflow 路线，默认）
python -m sqlagent.cli "数据库里一共有多少首歌曲？"

# ② 命令行（agent 路线 + 打印轨迹）
python -m sqlagent.cli "销量最高的 5 个流派" --route agent --trace

# ③ 交互模式
python -m sqlagent.cli --repl

# ④ 离线单测（52 项，零网络，不需要 Key）
pytest

# ⑤ 演示 notebook
jupyter nbconvert --to notebook --execute --inplace sql-agent-langchain.ipynb
```

notebook 共 10 节（环境 → 模型层 → LangGraph 拓扑 → 三道闸门 → 改写回环 → Agent 路径 → 逐层裁判 → 结构探针 → 交互输入框 → CLI），
已执行并保存输出。

---

## 7. 外部数据库接入

本项目的 SQL Agent **真的在连库、真的在跑 SQL**：数据来源是经典的 Chinook 示例库
（虚拟数字音乐商店，`chinook.db`，**不是程序生成的假数据**），模型全程**碰不到数据库**：

```
LLM 输出一个字符串（就是 SQL 文本）      ← 模型唯一的产物，纯文本
        │
        ▼
is_readonly(sql)        结构校验：非 SELECT/WITH → 直接放弃     ← 代码护栏
        │ ok
        ▼
parser.check_sql(sql)   试跑一次，确认语法 / 语义可执行
        ▼
parser.execute_sql(sql) conn.execute(text(sql))                ← 真正执行
        ▼
SQLAlchemy(sqlite/pysqlite) → sqlite3 → SQLite C 库 → 读 chinook.db
```

### 7.1 换库要改哪里

| 要改的东西 | 现状 | 换库时 |
|---|---|---|
| 库文件定位 | `find_up("chinook.db")` | 改文件名或改定位逻辑 |
| 连接串 `DB_URL` | `sqlite:///…` | 换成目标库 URL（**关键**），或用环境变量 `DB_URL` 覆盖 |
| 驱动 | sqlite 内置 | 外部库需 `pip install` 驱动（MySQL→`pymysql`、PG→`psycopg`） |
| 反射 `create_engine` / `inspect` | —— | **跨库通用，不用改** |
| 只读守卫 `is_readonly()` | 关键字白名单 | 方言相关，需复核 |
| 试跑 `check_sql()` | 真跑一次 | 方言相关 + 建议改只读事务 |

> 好消息：**反射层与流水线编排跨库通用**，换库主要动「连接串 + 提示词方言 + 守卫」三处。
> 定位逻辑集中在 [config.py](sqlagent/config.py) 的 `get_db_url()`，执行入口集中在
> [tools.py](sqlagent/tools.py) 的 `run_readonly_sql()`——**两条路自动同步**。

### 7.2 分阶段路线（Gate-based，先跑通再优化）

- **Gate 0 · 现状**：SQLite 本地文件，零配置。
- **Gate 1 · 换个 SQLite 文件**：把目标 `.db` 放到同目录，改 `find_up(...)` 的文件名。
  验收：能反射新库、整条流水线跑通。
- **Gate 2 · 接入外部库（务必用只读账号）**：装驱动；凭据进 `.env`，用 `DB_URL` 环境变量覆盖；
  **在数据库侧建只读账号**（MySQL `GRANT SELECT`、PG 建只读 role）——**第一道护栏放在数据库本身**，
  代码守卫只是第二道。
- **Gate 3 · 适配方言与守卫**：提示词从「SQLite 专家」改成目标方言（日期函数、`LIMIT`、
  `||` vs `CONCAT`、引号）；`is_readonly` 的「首词 SELECT/WITH + 禁写关键字」跨库基本通用但要回归复核；
  `check_sql` 的自动提交 → 改只读事务（`BEGIN` + `ROLLBACK`），或靠只读账号兜底。
- **Gate 4 · 大库可扩展性**：现在 `DBParser` 初始化会**逐表逐字段跑统计**——小库没问题，
  几百张表 / 上万字段的真实库会变成「初始化查询风暴」。改法：统计改**惰性**；给 LLM 的 schema
  上下文改**按问题检索候选表**，而非一次全灌。
- **Gate 5 · 产品化**：连接池 / 超时 / 重试；审计日志；凭据轮换；只读强约束演练。

### 7.3 三条红线

1. **凭据绝不进代码 / notebook** —— 一律 `.env` + `.gitignore`。
2. **只读双保险** —— 代码守卫 + 数据库只读账号，两者独立，任一失效还有另一道。
3. **大库别全量灌 schema** —— 否则提示词超长、token 爆炸、初始化拖死。

> 更完整的分阶段清单、方言对照表与换库冒烟脚本，见原目录
> [`04_SQL-Code-Agent-Demo/外部数据库接入指南.md`](../04_SQL-Code-Agent-Demo/外部数据库接入指南.md)（服务对象仍是同一套 DBParser 思路）。

---

## 8. 已知限制

- LLM 生成 SQL 属**概率性**，同一问题两次执行可能得到不同 SQL；靠「只读 + 校验闭环」兜底，不保证 100%。
- **L2 / L5 是 LLM 裁判**，本身也具概率性；已用「带参照物 + 只输出 JSON + 解析失败记 `None`」约束。
- **L4 引入 LLM 复核后不再是纯确定性判据**：形状不同时改由 LLM 判「口径是否一致」。
- **数字溯源只是诊断信号，不参与通过性**：已做「派生合计豁免 + 名次/编号豁免」抑制误报，仍有残余噪音。
- **未通过 ≠ 一定错**：L2 的 FAIL 可能只是口径分歧（见第 5 节 G7），最终谁对仍需人工判定。
- **验证集规模小**（12 题），要当硬门槛还需扩样；扩充只需往 `GOLDEN_SET` 加「问题 + 手写 `ref_sql`」再跑 `freeze_golden()`。
- **L2 在复杂结构上假阴性高发**：结构探针（5 题）显示 L2 亮灯 3/5，理由多为「缺 `ORDER BY`」「少了 `ROUND`」这类**化妆性差异**；且 LLM 裁判有 **run-to-run 抖动**，单次结论不能当硬门槛（见第 5 节「结构探针」）。
- **澄清反问为单轮**：`ambiguous` 分支给出澄清语后，需用户下一条消息继续；未做「反问→等待→续接」的状态机。

---

## 9. 相关文档

- [LANGCHAIN-FOR-PM.md](LANGCHAIN-FOR-PM.md) —— **面向零基础 PM 的 LangChain 入门**：用本项目讲清「LangChain 是什么、六大核心概念、怎么举一反三、AI PM 面试高频问题」。
- [MIGRATION.md](MIGRATION.md) —— 逐文件的**搬迁对照表**：原 notebook 的每个 cell 搬到本项目的哪个模块，哪些改了、哪些故意没改。
- [DESIGN.md](DESIGN.md) —— **设计取舍**：两条路径为何共存、「不是所有东西都该 LangChain 化」的边界、评测装置的选型。
- `sql-agent-langchain.ipynb` —— 把上述一切**跑给你看**的演示 notebook。
