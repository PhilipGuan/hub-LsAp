# SQL Agent Workflow (chinook.db)

参考 `sql-agent.ipynb`，把「数据库解析 → 选表 → 生成 → 校验 → 改写 → 执行 → 汇总」
串成一条**确定性流水线**，交付物为可直接运行的 Notebook：`sql-agent-workflow.ipynb`。

## 为什么是「确定性流水线」

7 个阶段是固定顺序的工程流水线，不需要让 LLM 自主决定「下一步做什么」。
因此每个阶段各一次独立 LLM 调用，编排与回环逻辑全部写在 Python 层：

- 每步输入/输出可断言、可打日志、可单步调试；
- 校验失败 → 改写重试的回环由 Python 控制（`MAX_REWRITE`），不依赖模型自觉；
- 失败时诚实返回原因，不编造数据。

## 架构

```
用户问题
   │
   ▼  ── 对接用户的三道闸门（见「ChatBI」小节）──
[A] 追问改写  rewrite_followup()   有历史时 → 自包含问题
   ▼
[B] 范围判定  scope_check()        in / out / ambiguous + 理由
   ▼
[C] 分支：out → 谢绝并提示能问什么；ambiguous → 反问澄清；in → 进入下列流水线
   │
   ▼
[0] 环境准备 ── 动态向上找 .env → 伪装成 OpenAI 兼容接口 → MODEL
   ▼
[1] 数据库解析 (DBParser) ── table_names / fields统计 / 外键 / 样例
   ▼
[2] Table 选择 ── LLM(问题 + 表清单+字段 + 外键关系) → JSON 候选表名
   ▼
[3] SQL 生成 ── LLM(问题 + 选中表 schema/样例/外键) → SQL
   ▼
[4] SQL 校验 ── is_readonly() 只读守卫 + parser.check_sql() → (ok, err)
   │        ok=False
   ▼◄──────────────┐
[5] SQL 改写 ── LLM(问题 + 原SQL + 错误 + schema) → 新SQL ── retry ≤3 ──┘
   │ ok=True
   ▼
[6] SQL 执行 ── parser.execute_sql() → 行结果
   ▼
[7] 结果汇总 ── LLM(问题 + SQL + 结果) → 自然语言答案
```

## 各阶段输出契约

| 阶段 | 输入 | 输出 |
|---|---|---|
| 数据库解析 | db_url | `table_names`、`get_table_fields()`、`get_data_relations()`、`get_table_sample()` |
| Table 选择 | 问题 + 表摘要 + 外键 | `["invoices", "invoice_items", ...]`（JSON） |
| SQL 生成 | 问题 + 选中表 schema | 单条 `SELECT` / `WITH` 语句 |
| SQL 校验 | sql | `(True, "ok")` / `(False, err)` + 只读守卫 |
| SQL 改写 | 问题 + 原SQL + 错误 | 修正后的 SQL |
| SQL 执行 | sql | `rows`（list of tuple） |
| 结果汇总 | 问题 + SQL + rows | 自然语言答案 |

## 关键设计点

1. **只读安全（结构性护栏）**：`is_readonly()` 先剔除注释与字符串字面量，再要求首词为
   `SELECT`/`WITH`、拒绝多条语句、拒绝 `INSERT/UPDATE/DELETE/DROP/ALTER/CREATE/...`。
   不依赖 LLM 自觉。
2. **校验 → 改写闭环**：失败时把「错误 + 原 SQL + 问题 + schema」回灌 LLM 重写，
   最多重试 3 次；仍失败则诚实返回失败原因。
3. **脱敏**：复用 `__file__`/逐级向上找 `.env` 的机制，源码中无明文 Key、无本机绝对路径。
4. **模型**：DeepSeek（`deepseek-v4-flash`），**显式走 Chat Completions**，关闭 tracing。

## 结构性取证：真的在跑 SQL（Structure vs Prompt）

用 notebook 里**真实的** `DBParser` / `is_readonly`（不经过 LLM）实跑取证，结论：

1. **真的有 SQL 在跑**——不是模型编出来的数字；
2. **拦写入的护栏是「代码结构」，不是「提示词」**。

### 执行链路（模型全程不碰数据库）

```
LLM 输出一个字符串（就是 SQL 文本）        ← 模型唯一的产物，纯文本
        │
        ▼
is_readonly(sql)        结构校验：非 SELECT/WITH → 直接放弃      ← 代码护栏
        │ ok
        ▼
parser.check_sql(sql)   试跑一次，确认语法 / 语义可执行
        ▼
parser.execute_sql(sql) conn.execute(text(sql))                 ← 真正执行
        ▼
SQLAlchemy(sqlite/pysqlite) → sqlite3 模块 → SQLite C 库 → 读 chinook.db 文件
```

### 实跑证据

| # | 验证项 | 结果 |
|---|---|---|
| ① | 引擎 | `sqlite:///…/chinook.db`，dialect `sqlite/pysqlite`，sqlite3 `3.49.1` |
| ② | `execute_sql("SELECT COUNT(*) AS tracks FROM tracks")` | `columns=['tracks']`，`rows=[(3503,)]` |
| ③ | 标准库 `sqlite3` 直连同一文件交叉验证 | `[(3503,)]`，与 ② 一致 |
| ④ | `is_readonly()` | `SELECT` / `WITH` 放行；`DELETE` / `DROP` / `UPDATE` / 多语句 **拦截** |
| ⑤ | 在副本上改数据 | genres `25 →（插一行）26`；原始库保持 `25`（证明读的是文件当前内容） |
| ⑥ | 直连副本执行 `DELETE` | `rowcount=1`（说明裸执行器本身不拦写） |

### Structure vs Prompt

| | Prompt 约束（概率性） | 本实现 Structure（确定性） |
|---|---|---|
| 做法 | 提示词里写「只准写 SELECT」 | `is_readonly()` 正则白名单：首词必须 `SELECT`/`WITH`、去注释与字符串字面量后再扫关键字、禁多语句 |
| 失败时 | 模型可能不听 | 返回 `(False, 原因)` → `run_workflow` **直接 `return`**，根本不调用 `execute_sql` |
| 结果 | 「希望」不写坏语句 | 写操作**在到达数据库之前就被代码挡掉**，与模型意愿无关 |

### 可加固点（暂未实施）

1. 把 `is_readonly` 也放进 `execute_sql()` 内部——现在是「编排层不调它」，改成「它自己也不执行写」，加第二道锁；
2. `check_sql()` 用 `engine.begin()`（自动提交）试跑——当前顺序是先 `is_readonly` 再 `check_sql`，所以安全；但顺序一旦被改动即有风险，可改为只读事务（`BEGIN` + `ROLLBACK`）试跑。

## 数据访问层选型：为什么用 SQLAlchemy Core 而不是 ORM

### 先厘清一个概念

SQLAlchemy 本身分两层，**ORM 不是它的对立面，而是它自带的可选上层**：

- **Core**：SQL 工具箱 —— `Engine`（连接池）、`text()`（文本 SQL）、表达式语言
  （`select()` / `func` / `Table` / `MetaData` 反射）。直接对着数据库和 SQL。
- **ORM**：建在 Core 之上的一层 —— 把「表」映射成 Python 类、把「行」映射成对象实例，
  再由框架**代你生成 SQL**。

所以「ORM vs SQLAlchemy」严格说是「**ORM 层 vs Core 层**」。本项目用的是 **Core**。

### 两者对比

| 维度 | ORM | Core（本项目） |
|---|---|---|
| 心智模型 | 领域对象（`class` ↔ 表） | 表 / SQL（`Table`、文本 SQL） |
| 查询写法 | `session.query(Track).filter(...)` | `select(...)` 或 `text(sql)` |
| 结果形态 | 映射后的对象实例 | 裸 `Row` / `tuple` |
| 主键外键 | `relationship()` 对象导航 | `inspect()` 取元数据，自己 JOIN |
| schema 迁移 | 配合 Alembic 很顺 | 无内置迁移 |
| 抽象程度 | 高（SQL 被隐藏 / 生成） | 低（SQL 可见、可控） |

### ORM 的优劣势

**优势**

- 开发效率高：增删改查像操作对象，省掉大量样板 SQL；
- 关系导航：`order.customer.name` 自动补 JOIN，不用手写连接；
- 类型 / IDE 友好：模型类有字段补全；配合 Alembic 做 schema 迁移；
- 业务一致性：实体集中定义，业务代码只跟对象打交道。

**劣势（对本项目尤其关键）**

- **SQL 不透明**：真正发给数据库的语句由框架生成，容易写出 N+1、意外全表扫描，调优要回看生成的 SQL；
- **复杂查询别扭**：多表聚合、窗口函数、`WITH` / `UNION` 用 ORM 表达常比原生 SQL 更绕；
- **会话有隐藏状态**：`Session` 的身份映射、脏检查、事务边界需要额外心智；
- **映射用不上时就是负债**：只读、只跑一条聚合 SQL 的场景，模型类纯属多余。

### Core 的优劣势

**优势**

- **SQL 透明可控**：模型输出一条文本 SQL → 守卫校验它 → 连接执行它，每一跳都看得见（呼应「结构性取证」）；
- **契合安全审计**：`is_readonly(text)` 的守卫点清晰；换成 ORM，SQL 是被「生成」的，守卫点无处安放；
- **反射即元数据**：`inspect()` / `Table(autoload_with=)` 直接读出 schema 喂给 LLM，无需先手写模型类；
- **依赖轻、确定性强**：无 `Session` 状态、无隐式查询，同一输入 → 同一链路。

**劣势**

- 手写 SQL 样板多；表名 / 列名以**字符串**出现，重构时没有编译期保护；
- 没有对象映射与关系导航，复杂业务的组装更啰嗦。

### 本项目为什么选 Core（四条理由）

1. **产品本身就是一个字符串**：核心产物是「一条只读 SQL + 它的结果」，需要的是**可控地执行 SQL**，
   而不是「把行映射成对象」—— ORM 的看家本领在这里用不上。
2. **安全护栏依赖「可见的 SQL」**：结构性取证那节已证明「拦写入的是代码结构，不是提示词」。
   守卫直接吃文本 SQL 最直接；用 ORM 会把这条链藏进框架里。
3. **schema 反射正好是 Core 的能力**：把表结构喂给 LLM 这一步，`inspect()` / `Table` 反射最省事，
   ORM 反而要先写死模型类。
4. **MVP / 不为不存在的需求设计**：只读问答不需要 CRUD 与领域模型，
   引入 ORM 只会增加会话状态与隐式行为，收益为零。

> 反过来看：如果这是个「订单 / 用户 / 库存」的**读写后台**，需要对象导航 + 迁移 + 复杂领域逻辑，
> 那 ORM 更合适。选型看场景 —— 不是 ORM 不好，是场景不同。

## 数据来源与外部数据库接入

本项目的 SQL Agent **真的在连库、真的在跑 SQL**。本节说明数据来源、执行链路，
以及未来如何更换数据源、接入外部真实业务库。

### 一、真的连库跑 SQL —— 数据来源与链路

**数据来源**：一个真实的 SQLite 文件 `chinook.db`，与 notebook 同目录。

```
-rw-------  884736 bytes  chinook.db
file: SQLite 3.x database, page size 1024, 864 pages, UTF-8
```

它是经典的 **Chinook 示例库**（一个虚拟数字音乐商店），**不是程序生成的假数据**。实跑取证：

```
表：albums artists customers employees genres invoice_items invoices
    media_types playlist_track playlists tracks (+ sqlite_sequence / sqlite_stat1)
计数：tracks=3503  albums=347  artists=275  customers=59  invoices=412  employees=8
聚合：USA 91 / Canada 56 / France 35 / Brazil 35 / Germany 28
```

这些数字与「逐层裁判验证」中 `GOLDEN_SET` 冻结的 `ref_rows`（tracks 3503、customers 59、
Germany 28）完全对上 —— 裁判验证读的就是这个文件。

**链路（模型全程不碰数据库）**

```
LLM 输出一个字符串（就是 SQL 文本）   ← 模型唯一的产物，纯文本
        │
is_readonly(sql)                      ← 代码护栏：非 SELECT/WITH 直接放弃
        │ ok
parser.check_sql(sql)                 ← 试跑一次
        ▼
parser.execute_sql(sql)  conn.execute(text(sql))   ← 真正执行
        ▼
SQLAlchemy(sqlite/pysqlite) → sqlite3 模块 → SQLite C 库 → 读 chinook.db 文件
```

**代码中的三个接入点**

| 环节 | 位置 | 说明 |
|---|---|---|
| 库文件定位 | `_db_path = find_up("chinook.db")` | 从 CWD 逐级向上找文件 |
| 连接串 | `DB_URL = f"sqlite:///{_db_path}"` | **唯一入口，换库就改这里** |
| 反射 / 建连 | `create_engine(DB_URL)` → `DBParser(DB_URL)` | 跨库通用，换库不用改 |

### 二、「一段 SQL + Notebook」如何跑出数据检索

最小骨架只有三步：**建连接 → 发 SQL → 取结果**。

最简版（只用标准库，零依赖）：

```python
import sqlite3, pandas as pd
con = sqlite3.connect("chinook.db")                  # 连库
pd.read_sql("SELECT BillingCountry, COUNT(*) AS c "  # 发 SQL
            "FROM invoices GROUP BY 1 ORDER BY c DESC LIMIT 5", con)
```

本项目版（SQLAlchemy Core，多一层「可控性」）：

```python
from sqlalchemy import create_engine, text
engine = create_engine("sqlite:///chinook.db")
with engine.connect() as conn:
    rows = conn.execute(text(sql)).fetchall()        # sql 是字符串
```

**三个角色的分工**（理解这个模块的关键）：

| 角色 | 职责 | 不负责 |
|---|---|---|
| LLM | 把「人话问题」翻译成 SQL **文本** | 碰不到数据库 |
| 守卫 `is_readonly` / `check_sql` | 检查这段文本安全、可执行 | 不生成 SQL |
| 数据库 | 执行 SQL、返回行 | 不做判断 |

> 小提醒：`inspector.get_table_names()` 会把 `sqlite_sequence`、`sqlite_stat1`
> 两个 SQLite 内部表也列进去，一起喂给 LLM。换库时注意过滤。

### 三、换数据源 / 接入外部真实业务库

**先看「要改哪里」**

| 要改的东西 | 现状 | 换库时 |
|---|---|---|
| 库文件定位 | `find_up("chinook.db")` 硬编码文件名 | 改文件名或改定位逻辑 |
| 连接串 `DB_URL` | `sqlite:///…` | 换成目标库 URL（**关键**） |
| 驱动 | sqlite 内置 | 外部库需 `pip install` 驱动 |
| 反射 `create_engine` / `inspect` | —— | **跨库通用，不用改** |
| 只读守卫 `is_readonly()` | 关键字白名单 | 方言相关，需复核 |
| 试跑 `check_sql()` | `engine.begin()` | 方言相关 + 建议改只读事务 |

> 好消息：**反射层与流水线编排跨库通用**，换库主要动「连接串 + 提示词方言 + 守卫」三处。

**分阶段路线（Gate-based，先跑通再优化）**

- **Gate 0 · 现状**：SQLite 本地文件，零配置。
- **Gate 1 · 换个 SQLite 文件**（先验证「换源」这件事本身）：把目标 `.db` 放到同目录，
  改 `find_up(...)` 的文件名。不涉驱动 / 网络 / 凭据。验收：能反射新库、整条流水线跑通。
- **Gate 2 · 接入外部库（务必用只读账号）**：装驱动（MySQL→`pymysql`、PostgreSQL→`psycopg`、
  SQL Server→`pyodbc`）；凭据进 `.env`，代码改成 `DB_URL = os.environ["DB_URL"]`；
  **在数据库侧建只读账号**（MySQL `GRANT SELECT`、PG 建只读 role）—— 这是
  「结构性保证优于提示词」的延伸，**第一道护栏放在数据库本身**，代码守卫只是第二道。
  风险：网络可达 / 超时 / 连接串泄漏（绝不能打印）。
- **Gate 3 · 适配方言与守卫**：SQL 生成提示词从「SQLite 专家」改成目标方言
  （日期函数、`LIMIT`、字符串拼接 `||` vs `CONCAT`、引号）；`is_readonly` 的
  「首词 SELECT/WITH + 禁写关键字」跨库基本通用但要回归复核；`check_sql` 的
  `engine.begin()` 会自动提交 → 改只读事务（`BEGIN` + `ROLLBACK`），或靠只读账号兜底。
  验收：小评测集（复用 `GOLDEN_SET` 思路）在目标库通过。
- **Gate 4 · 大库可扩展性**（从 demo 到真实库必须跨的坎）：现在 `DBParser` 初始化会
  **逐表逐字段跑统计**（distinct / 众数 / max / min / 随机样例）—— 小库没问题，
  几百张表 / 上万字段的真实库会变成「初始化查询风暴」。改法：统计改**惰性**
  （只对选中的表算）；给 LLM 的 schema 上下文改**按问题检索候选表**，而非一次全灌。
- **Gate 5 · 产品化**：连接池参数、超时与重试；审计日志（谁在何时跑了哪条 SQL）；
  凭据轮换 / 密钥管理；把只读强约束真正落地并做演练。

### 三条红线

1. **凭据绝不进代码 / notebook** —— 一律 `.env` + `.gitignore`。
2. **只读双保险** —— 代码守卫 + 数据库只读账号，两者独立，任一失效还有另一道。
3. **大库别全量灌 schema** —— 否则提示词超长、token 爆炸、初始化拖死。

> 完整可执行版（分阶段清单、方言对照表、换库冒烟脚本）见同目录
> [`外部数据库接入指南.md`](外部数据库接入指南.md)。

## 与参考实现的角色对照

| 参考中的角色 / 工具 | 本实现的等价模块 |
|---|---|
| `@function_tool parse_table` | `table_catalog()` / `schema_context()` |
| `SQLTableSelectAgent`（"SQL Planer"） | `select_tables()` |
| `SQLGeneratorAgent`（参考里是空壳） | `generate_sql()` |
| 执行 sql | `is_readonly()` + `check_sql()` + `execute_sql()` |
| sql rewrite | `rewrite_sql()` |
| `SQLResultSummaryAgent`（参考里是空壳） | `summarize()` |
| `ask_gpt()` | `chat()` |
| 造题 prompts（question / answer） | `simulate_question()` / `simulate_questions()` |
| 末尾 5 步 pipeline 注释 | `run_workflow()` |
| ——（本实现新增） | 只读守卫、`scope_check()`、`ChatBI`、`render_result()` |

唯一没照搬**形态**的是 `parse_table`：参考用 `function_tool` 让选表 Agent **按需**取表字段，
本实现改为选表后**一次性**把 schema 灌进提示词——功效等价、更确定、少一轮工具往返。

## 问题模拟生成器（QuestionSimulator）

参考 notebook 里「对着表结构用 LLM 造题」的循环，在本实现中就是**问题模拟生成器**：

| 函数 | 作用 |
|---|---|
| `simulate_question(parser, table, style, user_style)` | 对单表造 1 题；给出参考 SQL 与真值 |
| `simulate_questions(parser, tables, n_per_table, style, user_style)` | 批量造题；只保留「可执行且答案为单值」的题 |
| `simulate_out_of_scope(n)` | 造**越界负样本**（与库无关的问题），用于测试范围守门人 |

- `style` 支持 `count`（单值统计）/ `group`（分组对比）；
- `user_style=True` 会把技术口吻的题改写成普通用户会问的说法；
- `build_eval_set()` 只是它的**特化调用**：`simulate_questions(..., style="count")`。

> 造题与判分用同类模型，存在**自证偏差**；它只用于构造测试 / 演示数据，不代表真实用户分布。

## ChatBI（多轮对话 + 用户对接）

单轮链路之上，`ChatBI.ask()` 串起**三道闸门**，真·对接用户：

```
用户输入
  → [A] rewrite_followup()   有历史时把追问改写成自包含问题
  → [B] scope_check()        判定 in / out / ambiguous，并给理由
  → [C] 分支
        out       → 谢绝，并提示「可以问销量 / 客户 / 员工 / 曲目 / 专辑 / 流派」
        ambiguous → 反问澄清（附一个具体的澄清示例）
        in        → 交给 7 阶段流水线 → 表格 / 图表 / 分析
```

**关键：闸门加在生成 SQL 之前**，否则越界问题会被硬编成一条 SQL。

- **范围判定是结构性闸门**：守门人只输出 JSON；并做**结构兜底**——若它声称 `in` 却落不到
  任何真实表，就强制降级为 `ambiguous`，避免硬编 SQL。
- `ChatBI` 维护会话历史（最近 5 轮），把三步串起来；
- `render_result()`：结果渲染为表格；结构为「1 类别列 + 1 数值列」时**自动出柱状图**。

实测：`in` 正向题（含依赖上下文的追问）、`ambiguous` 缺主语题（如「有多少？」→ 反问「你想统计什么数量？」）、`out` 越界题（写诗 / 天气）三类均按预期分流。

### 用户输入方式

notebook 里有三种触发方式，按「产品化程度」递增：

1. **交互输入框（ipywidgets）**——`### 交互输入框` cell：输入框 + 「提问」按钮，
   **不阻塞内核**，`Run All` / `nbconvert` 也能正常跑完；在 Jupyter 里直接敲问题即可。
2. **`input()` 循环**——把 `INTERACTIVE` 改为 `True` 后运行对应 cell（单轮 / 多轮各一处）；
   但无头执行时 `input()` 会一直阻塞，故默认 `False`。
3. 把 `ChatBI` 抽成后端函数、外套 Streamlit / FastAPI，才是真·产品形态（本仓库未做）。

> 图表已配置中文字体（`Arial Unicode MS` / `Heiti SC`）并兜底屏蔽渲染 warnings——
> 避免「缺字警告」把本机绝对路径写进 notebook 输出。

## 逐层裁判验证（JudgeAgent + 冻结验证集）

规模化使用 SQL Agent 之前，先用「带答案的验证集」**逐层**校验取数逻辑是否严谨正确。

**为什么要逐层**：一次问答的错误可能注入在 5 个跳跃点，只看终点值既无法定位，
也会放过「结论对、过程错」（选错表却恰好数值撞对）：

```
问题 ─①→ 选表 ─②→ 生成 SQL ─③→ 校验 ─④→ 执行 ─⑤→ 汇总回答
```

**两条第一性原理**

1. **可判定 vs 需判断**：数值是否相等、SQL 能否执行、有无写操作、答案里的数字能否溯源
   —— 这些用**代码断言**即可；只有「这条 SQL 是否忠实表达了问题」才交 LLM 裁判。
2. **LLM 裁判必须带参照物**：喂「问题 + 待审 SQL + 权威 SQL」「问题 + 权威结果 + 参考要点」，
   把「你觉得对吗」变成「对照批改（rubric + reference）」，否则退化成两个模型对喷。

**验证集冻结**：`GOLDEN_SET` 一次编好、只读；`ref_rows` 由手写 `ref_sql` 经
**双引擎（SQLAlchemy + 原生 `sqlite3`）交叉验证**后固化；评测只读、绝不重算 ——
既保证可复现，也让「答案」独立于「答题模型」，避免自证偏差。

**5 层裁判**

| 层 | 阶段 | 裁判函数 | 类型 | 判据 |
|---|---|---|---|---|
| L0 | 闸门 | `judge_scope` | 确定性 | scope 标签与权威一致 |
| L1 | 选表 | `judge_tables` (+LLM) | 确定 + LLM | 是否覆盖必需表；多余表交 LLM 判是否无害 |
| L2 | 生成 | `judge_sql` | LLM | 与权威 SQL 是否语义等价 |
| L3 | 校验 | `judge_guard` | 确定性 | 是否产出可执行 SQL（只读守卫放行） |
| L4 | 执行 | `judge_result` (+LLM) | 确定 + LLM | 结果集等价（行序无关 + 数值容差）；判不一致时交 LLM 语义复核 |
| L5 | 汇总 | `judge_answer` (+数字溯源) | LLM + 确定性 | 要点覆盖 + 数字能否溯源 |

> 端到端通过 = 各层「有判定」的项全部通过；裁判返回 `None`（如 JSON 解析失败）表示
> 「未给出结论」，不计入通过性，但会在明细里显式暴露，不掩盖问题。

**两处「确定性 → LLM」升级**（只在代码判不死时才动 LLM）

- **L4**：结果集「严格相等」只是**充分不必要条件** —— Agent 可能把姓名拆成 `FirstName` /
  `LastName` 两列，语义没错但形状与权威不同。故确定性比较判不一致时，升级 LLM 做「口径是否一致」复核。
- **L1**：选表覆盖了必需表但**多选了表**时，交 LLM 判多余表是否改变口径。

验证集共 12 题（`in` 9 / `out` 2 / `ambiguous` 1），覆盖 count / filter / join / group / topk
与三道闸门；`freeze_golden()` 可随时用双引擎重建 `ref_rows`。

实测（`deepseek-v4-flash`，一次跑全量）：L0 100% / L1 100% / L2 91.7% / L3 100% /
L4 100% / L5 100%，端到端 11/12；唯一未通过项是 G7 的 **L2 口径分歧**（Agent 用
`LEFT JOIN` + `Title` 过滤，权威用 `INNER JOIN`），结果恰好相同 —— 这正是「逐层」的价值：
**定位到具体一跳并给出可读理由**，而不是只报一个「对/错」。

## 运行方式

```bash
# 1) 安装依赖（本次新增 sqlalchemy、tabulate；其余已具备）
pip install sqlalchemy tabulate

# 2) 确认项目根目录 .env 内含 DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL / DEEPSEEK_MODEL

# 3) 打开并运行 sql-agent-workflow.ipynb；或命令行无头执行：
jupyter nbconvert --to notebook --execute --inplace sql-agent-workflow.ipynb
```

## 任务规划（已完成项打勾）

- [x] 装依赖 `sqlalchemy`、`tabulate`，验证 DeepSeek Key 可用
- [x] 环境准备 cell（脱敏加载 `.env`）
- [x] `DBParser` + `is_readonly()` 只读守卫
- [x] `chat()` 封装 + `select_tables / generate_sql / rewrite_sql / summarize`
- [x] `run_workflow()` 编排（7 阶段串接 + 改写回环 + 轨迹打印）
- [x] 交互 Demo cell（内置示例问题 + 键盘输入入口）
- [x] 问题模拟生成器 `simulate_question / simulate_questions / simulate_out_of_scope`
- [x] 小型评测 cell（复用生成器造题 + 数据库真值校验 → 执行成功率 / 结果匹配率）
- [x] ChatBI 多轮对话（追问改写 + 会话历史）+ 结果表格 / 自动柱状图
- [x] 范围判定 `scope_check()`（in / out / ambiguous + 结构兜底）与三分支用户对接
- [x] 交互输入框（ipywidgets，不阻塞内核）
- [x] `jupyter nbconvert --execute` 实跑验证
- [x] 结构性取证（Structure vs Prompt）收录进本 README
- [x] 逐层裁判验证（JudgeAgent + 冻结验证集，L0–L5）

## 已知限制

- LLM 生成 SQL 属**概率性**，同一问题两次执行可能得到不同 SQL；靠「只读 + 校验闭环」兜底，
  但不保证 100% 正确。
- 评测集由 LLM 合成，存在**自证偏差**（用同一类模型造题、又用同类模型答题）；
  指标只用于横向对比流程是否有效，不等同于真实业务准确率。
- Table 选择为**单轮判断**，跨多表复杂 join 时可能漏表；已在表摘要中喂入外键关系缓解。
- `check_sql` 采用「真跑一次」的方式校验；配合只读守卫，写入类语句在执行前已被拦下。
- `execute_sql()` 本身是**裸执行器**（不校验只读），护栏只在 `run_workflow` 的 `is_readonly` 一步；
  若有代码绕过编排直接调用 `execute_sql()`，写语句会被真的执行（加固方案见「结构性取证 → 可加固点」）。
- ChatBI 的**追问改写**同样是概率性判断：边界模糊的追问（如省略主语）可能与预期不符；
  本实现用「[A] 先改写 → [B] 再判定」的顺序缓解，但不保证 100%。
- **范围判定**（`scope_check()`）同为 LLM 概率性判断：本实现用「结构兜底」（`in` 的 `tables`
  必须落库，否则降级 `ambiguous`）降低误判，但不保证 100%。
- `scope_check()` 与 `select_tables()` 有一次**重复的选表调用**（都基于同一 catalog）；
  可合并以省一次 LLM 调用（已知优化点）。
- 澄清反问为**单轮**：`ambiguous` 分支给出澄清语后，需用户下一条消息继续；未做「反问→等待→续接」的闭环状态机。
- **逐层裁判的验证集规模小**（12 题，覆盖 count / filter / join / group / topk / 闸门三类），
  要当硬门槛还需扩样；扩充只需往 `GOLDEN_SET` 加「问题 + 手写 `ref_sql`」再跑 `freeze_golden()`。
- **L2 / L5 是 LLM 裁判**，本身也具概率性：已用「带参照物 + 只输出 JSON + 解析失败记 `None`」约束，
  但不保证 100% 判准。
- **L4 引入 LLM 复核后不再是纯确定性判据**：形状不同时改由 LLM 判「口径是否一致」，
  结果从「字节相等」变为「语义等价」，需接受这层 LLM 判断。
- **数字溯源只是诊断信号，不参与通过性**：已做「派生合计豁免 + 名次/编号豁免」抑制误报，仍有残余噪音。
- **未通过 ≠ 一定错**：L2 的 FAIL 可能只是口径分歧（如 `LEFT JOIN` + `Title` 过滤 vs `INNER JOIN`），
  装置只负责定位到具体一跳并给出理由，最终谁对仍需人工判定。
- **权威口径由人写 `ref_sql` 定义**：若口径本身有争议，裁判结论也随之有争议 —— 这是验证集的天花板。
