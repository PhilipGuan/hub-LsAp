# SQL Agent Workflow（基于 Chinook 数据库）

参考 `sql-agent.ipynb` 的思路，把「自然语言 → SQL → 自然语言」拆成一个**可控、可观测**的多步工作流，
而不是把所有事情塞进一次 Agent 调用。

```
数据库解析 → table 选择 → SQL 生成 → SQL 校验 → SQL 改写 → SQL 执行 → 结果汇总
```

- 只依赖 Python 标准库 + `openai` SDK
- 数据库默认**只读**打开（URI `mode=ro` + `PRAGMA query_only`）
- 每一步都有 trace（耗时、输入输出摘要），失败时能定位是哪一个环节的问题
- 不依赖 SQLAlchemy / pandas，`chinook.db` 开箱即用

## 目录结构

```
04_SQL-Code-Agent-Demo/
├── chinook.db                # 示例数据库
├── run_sql_agent.py          # 命令行入口
├── selftest_offline.py       # 离线自测（不调用大模型）
├── sql-agent-workflow.ipynb  # 分步骤演示 notebook
├── .env.example              # 配置模板
└── sql_agent/
    ├── config.py             # 环境变量 / .env 配置
    ├── db_parser.py          # 数据库解析、schema 生成、SQL 校验与执行
    ├── llm.py                # OpenAI 兼容模型客户端（重试 + token 统计）
    ├── prompts.py            # 各步骤 Prompt
    └── workflow.py           # 工作流编排
```

## 快速开始

### 1. 安装依赖

```bash
pip install openai
```

### 2. 配置模型

复制 `.env.example` 为 `.env`，填入 API Key：

```bash
cp .env.example .env
```

```ini
SQL_AGENT_API_KEY=sk-xxxxxxxx
SQL_AGENT_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
SQL_AGENT_MODEL=qwen-plus
```

也可以直接设置环境变量：`SQL_AGENT_API_KEY`、`DASHSCOPE_API_KEY` 或 `OPENAI_API_KEY`
（优先级：进程环境变量 > `.env`）。

### 3. 运行

```bash
# 查看数据库结构（不需要 API Key）
python run_sql_agent.py --schema
python run_sql_agent.py --describe invoices

# 离线自测（不需要 API Key）
python selftest_offline.py

# 直接校验并执行一条 SQL（跳过 LLM，便于调试校验/执行环节）
python run_sql_agent.py --sql "SELECT Name, COUNT(*) FROM genres GROUP BY Name"

# 单个问题
python run_sql_agent.py "哪个流派的曲目数量最多？列出前 5 个流派及其曲目数。"

# 指定表，跳过 table 选择
python run_sql_agent.py "2009 年每个月的总销售额是多少？" --tables invoices

# 运行内置示例
python run_sql_agent.py --demo

# 交互式问答
python run_sql_agent.py --interactive

# JSON 输出（方便接入其他系统）
python run_sql_agent.py "销售额最高的 5 位客户是谁？" --json
```

也可以打开 `sql-agent-workflow.ipynb`，按「数据库解析 → 完整工作流 → 查看 trace」的顺序交互式运行。

终端会逐步打印每一步的耗时和摘要，最后输出 SQL、结果表格、自然语言答案和 token 消耗。

## 工作流细节

### 1. 数据库解析（`DBParser`）

读取 `sqlite_master`，对每张表收集：

- 字段名、类型、是否主键、是否可空、默认值
- 外键关系（支持复合外键）
- 行数
- 每个字段的统计：唯一值数、空值数、min/max、Top 3 常见值
- 前 2 行样例数据

这些信息会渲染成 Markdown 表格，作为后续 prompt 的上下文。

### 2. table 选择

把所有表的**紧凑清单**（表名、行数、字段、PK/FK）和外键关系交给模型，要求只返回 JSON：

```json
{"tables": ["genres", "tracks"], "reason": "按流派统计曲目数"}
```

返回的表名会做大小写容错和存在性过滤；解析失败时回退为全表选择，保证流程不中断。

### 3. SQL 生成

只把**选中表**的详细 schema（字段统计 + 样例行）和关系交给模型，降低 token 和幻觉。
Prompt 中约束：只能使用给定字段、只生成一条只读查询、聚合列必须有别名、明细查询加 `LIMIT`。

### 4. SQL 校验

在执行前做三层检查：

1. 去掉 Markdown 围栏和结尾分号，必须是单条语句；
2. 必须以 `SELECT` / `WITH` 开头，且不包含 `INSERT/UPDATE/DELETE/DROP/ALTER/PRAGMA/ATTACH...`；
3. 用 `EXPLAIN QUERY PLAN <sql>` 编译，提前发现语法错误、表名/字段名错误。

字符串字面量和注释会先被屏蔽，因此 `WHERE name = 'delete'`、`SELECT ';'` 不会被误判。

### 5. SQL 改写

校验或执行失败时，把「原问题 + schema + 失败 SQL + 报错 + 历史失败记录」交给模型改写，
最多重试 `SQL_AGENT_MAX_REPAIR` 次（默认 3）。历史记录可以避免模型反复犯同一个错误。

### 6. SQL 执行

- 只读连接 + `query_only`，从连接层杜绝写操作
- `fetchmany(max_rows + 1)` 判断是否截断，避免把整张大表拉进内存
- `set_progress_handler` 设置查询超时，防止笛卡尔积等慢查询卡死

### 7. 结果汇总

把问题、SQL、列名、行数、结果 Markdown（最多 50 行）交给模型，要求：

- 先结论后数字
- 多行结果只概括 Top 项
- 只依据给定数据，不编造
- 结果为空 / 被截断时明确说明

## 编程调用

```python
from sql_agent import Settings, SQLAgentWorkflow

settings = Settings.load()
with SQLAgentWorkflow("chinook.db", settings=settings) as workflow:
    result = workflow.run("消费金额最高的 5 位客户是谁？")

print(result.sql)
print(result.columns, result.rows)
print(result.answer)
print(result.to_dict())      # 包含 traces、token 消耗，便于日志/评测
```

也可以单独调用某一步：

```python
from sql_agent.db_parser import DBParser

with DBParser("chinook.db") as parser:
    parser.parse_all()
    print(parser.table_overview_markdown())
    print(parser.table_schema_markdown("invoices"))

    ok, error = parser.validate_sql("SELECT COUNT(*) FROM invoices")
    columns, rows, truncated = parser.execute_sql(
        "SELECT BillingCountry, SUM(Total) FROM invoices GROUP BY 1",
        max_rows=10,
    )
```

## 配置项

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `SQL_AGENT_API_KEY` | 无 | API Key（也支持 `DASHSCOPE_API_KEY` / `OPENAI_API_KEY`） |
| `SQL_AGENT_BASE_URL` | DashScope 兼容地址 | OpenAI 兼容接口地址 |
| `SQL_AGENT_MODEL` | `qwen-plus` | 模型名 |
| `SQL_AGENT_MAX_ROWS` | `100` | 最大返回行数 |
| `SQL_AGENT_MAX_REPAIR` | `3` | SQL 改写最大次数 |
| `SQL_AGENT_QUERY_TIMEOUT` | `30` | 单条 SQL 超时（秒） |
| `SQL_AGENT_TEMPERATURE` | `0` | 采样温度 |
| `SQL_AGENT_LANGUAGE` | `中文` | 汇总语言 |

## 与参考 notebook 的差异

| 参考 notebook | 本实现 |
|---|---|
| SQLAlchemy + pandas 解析 | 内置 `sqlite3`，零额外依赖 |
| 用 DB 全量数据反复跑统计 | 元信息 + 统计缓存，按需计算 |
| `check_sql` 直接执行 SQL 验证 | 先静态校验，再用 `EXPLAIN QUERY PLAN` 编译（不产生副作用） |
| 单次生成，失败重试 | 显式的「校验 → 改写」循环，保留失败历史 |
| 结果直接拼字符串 | 结果汇总 agent 生成自然语言，并带空结果/截断提示 |

## 扩展方向

- **多轮对话**：把历史问题和 SQL 存入 `SQLAgentWorkflow`，在 table 选择/SQL 生成时带上上下文
- **Schema RAG**：表很多时，先用向量检索缩小候选表，再做 table 选择
- **评测集**：参考 notebook 的「模拟生成问题」流程，批量生成 `(question, gold_sql)`，统计执行准确率
- **可视化**：在 `SQLAgentResult.rows` 后接一个 chart agent，把结果画成柱状图/折线图
- **权限控制**：按用户角色限制可访问的表和字段，在 `DBParser` 外层加白名单
