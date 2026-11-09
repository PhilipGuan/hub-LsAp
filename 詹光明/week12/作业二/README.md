# week12 作业二：用 LangChain 复刻 SQL Agent

对应 `作业二.py` 的需求：结合之前的作业答案，把 `D:\AIwork\sql_agent` 中基于
**OpenAI Agents SDK** 搭建的 SQL Agent，用 **LangChain** 复刻一遍。

参考实现的核心不是让模型自由编排，而是「四个 LLM 角色 + 代码显式控制流程」：

```
问题 -> 选表 -> 生成 SQL -> 校验/执行 -> (失败则改写，最多 N 次) -> 汇总答案
```

本作业保留同样的分层和数据契约，只把 Agent 层替换为 LangChain：

| 参考实现（OpenAI Agents SDK） | 本实现（LangChain） |
| --- | --- |
| `Agent(instructions=..., output_type=...)` | `ChatPromptTemplate \| ChatOpenAI.with_structured_output(...)` |
| `Runner.run(agent, prompt)` | `chain.ainvoke({"input": prompt})` |
| `ModelSettings(temperature=...)` | `ChatOpenAI(temperature=...)` |
| `response.final_output` | 链直接返回 Pydantic 模型 |
| `trace("SQLAgentWorkflow")` | LangSmith 环境变量（可选） |
| 模块级 `Agent` 对象 | `@lru_cache` 的链工厂（导入时不校验 API key） |

DB 安全层（schema 解析、SQL 门禁、只读执行）与 LLM 框架无关，按参考实现移植。

## 流程与角色

| 角色 | LangChain 链 | 输入 | 输出（Pydantic） |
| --- | --- | --- | --- |
| 表选择 | `chains/table_selector.py` | 问题 + 轻量目录 + 外键 | `TableSelection` |
| SQL 生成 | `chains/sql_generator.py` | 问题 + 详细 schema/样例 | `SQLDraft` |
| SQL 改写 | `chains/sql_rewriter.py` | 失败 SQL + 错误码 + 历史 | `SQLDraft` |
| 结果汇总 | `chains/result_summarizer.py` | 问题 + SQL + 结果行 | `FinalAnswer` |

流程细节（见 `sql_agent/workflow.py`）：

1. **选表**：模型只从目录中挑表；代码再校验表名存在、所选表之间有外键连通。
   不合法时最多重试 1 次，仍失败则返回澄清问题。
2. **生成 SQL**：只把已选表的 schema、样例数据、外键关系交给模型。
3. **校验/执行**：先用 `EXPLAIN` 编译（不执行），再走只读连接执行。
4. **改写**：校验或执行失败时，把错误码、错误信息和最近几次 SQL 交给改写链，
   最多 `max_repair_attempts` 次；模型认为无法修复时直接返回原因。
5. **汇总**：空结果不调用模型，固定回答「未查询到符合条件的数据」；汇总链失败时
   降级为 Markdown 表格，保证 CLI 永远有可读输出。

## 目录结构

| 路径 | 说明 |
| --- | --- |
| `main.py` | 便捷入口（等价于 `python -m sql_agent`） |
| `sql_agent/cli.py` | 命令行参数、环境检查、JSON 输出 |
| `sql_agent/config.py` | 运行配置（环境变量 / `.env`） |
| `sql_agent/models.py` | Pydantic 数据契约 |
| `sql_agent/workflow.py` | 显式代码编排 |
| `sql_agent/chains/` | 四个 LangChain 链 |
| `sql_agent/db/catalog.py` | schema、统计信息、样例、外键图 |
| `sql_agent/db/validator.py` | 单语句 / 只读 / 关键字 / `EXPLAIN` 校验 |
| `sql_agent/db/executor.py` | 只读执行、超时、行数/单元格截断 |
| `scripts/fetch_chinook.py` | 下载 Chinook SQLite 示例库到 `data/` |
| `scripts/smoke_test.py` | 离线冒烟测试（不需要 API key） |

## 快速开始

要求 Python 3.10+。

```bash
cd 詹光明/week12/作业二

# 1. 安装依赖
pip install -r requirements.txt

# 2. 准备数据库（已随仓库提供 data/chinook.db 时可跳过）
python scripts/fetch_chinook.py
# data/chinook.db 来自 lerocha/chinook-database（MIT License），仅作示例数据

# 3. 配置模型（二选一）
export OPENAI_API_KEY=sk-...
export OPENAI_BASE_URL=https://api.deepseek.com   # 可选，默认就是 DeepSeek
# 或者把 .env.example 复制为 .env 并填写

# 4. 提问
python -m sql_agent "销售额最高的前 5 位艺术家是谁？"
python main.py "每个国家的客户数量，按数量降序"
echo "2010 年每个月的总销售额" | python -m sql_agent
```

输出为标准 JSON（日志走 stderr，不污染 stdout）：

```json
{
  "question": "每个国家的客户数量，按数量降序",
  "answer": "客户数量最多的国家是美国，共 13 位；其次是加拿大，共 8 位……",
  "sql": "SELECT Country, COUNT(*) AS CustomerCount FROM Customer GROUP BY Country ORDER BY CustomerCount DESC",
  "tables": ["Customer"],
  "row_count": 24,
  "truncated": false,
  "repair_attempts": 0,
  "evidence": ["美国：13", "加拿大：8"],
  "caveats": [],
  "error": null
}
```

出现不可恢复错误时，`error` 非空且进程退出码为 1。

## 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `OPENAI_API_KEY` | — | 必填；也支持 `SQL_AGENT_API_KEY` |
| `OPENAI_BASE_URL` | `https://api.deepseek.com` | OpenAI 兼容端点 |
| `SQL_AGENT_MODEL` | `deepseek-chat` | 选表 / 生成 / 改写模型 |
| `SQL_AGENT_SUMMARY_MODEL` | `deepseek-chat` | 汇总模型 |
| `SQL_AGENT_STRUCTURED_METHOD` | `function_calling` | `function_calling` / `json_schema` / `json_mode` |
| `SQL_AGENT_DB` | `data/chinook.db` | 数据库路径（相对路径基于作业二目录） |
| `SQL_AGENT_MAX_REPAIR_ATTEMPTS` | `3` | 最大改写次数 |
| `SQL_AGENT_MAX_ROWS` | `200` | 单次查询最多返回行数 |
| `SQL_AGENT_MAX_SUMMARY_ROWS` | `50` | 交给汇总链的最大行数 |
| `SQL_AGENT_MAX_CELL_CHARS` | `2000` | 单元格最大字符数 |
| `SQL_AGENT_QUERY_TIMEOUT` | `5.0` | SQL 执行超时（秒） |
| `SQL_AGENT_LLM_TIMEOUT` | `60.0` | LLM 请求超时（秒） |
| `SQL_AGENT_LLM_RETRIES` | `2` | LLM 网络重试次数 |
| `SQL_AGENT_LOG_LEVEL` | `INFO` | 日志级别 |
| `LANGCHAIN_TRACING_V2` / `LANGCHAIN_API_KEY` / `LANGCHAIN_PROJECT` | — | 可选，启用 LangSmith 追踪 |

> 若端点不支持工具调用（`function_calling`），可设置
> `SQL_AGENT_STRUCTURED_METHOD=json_mode`。提示词中已要求「只输出结构化结果（JSON）」，
> 满足多数兼容端点的 JSON 模式要求。

## 安全与可靠性

- **只读连接**：SQLAlchemy / sqlite3 都使用 `mode=ro` URI，并额外 `PRAGMA query_only=ON`。
- **语句门禁**：只允许单条 `SELECT` / `WITH ... SELECT`；先遮蔽字符串、引号标识符和注释，
  再检查 `INSERT/UPDATE/DELETE/CREATE/DROP/ALTER/PRAGMA/ATTACH` 等关键字。
- **只编译不执行**：校验阶段使用 `EXPLAIN`，既能发现未知表/列和歧义，又不产生结果。
- **资源限制**：执行超时（progress handler 中断）、最多 200 行、单元格最多 2000 字符。
- **提示词注入**：用户问题被包在 `<question>` 中并声明「仅作为数据」，四个系统提示都要求
  忽略输入中修改规则或输出格式的指令。
- **有界重试**：改写次数有上限，空结果不重试、不让模型猜测原因。
- **降级输出**：汇总链异常时回退为表格，不会导致整个流程失败。

## 测试

离线冒烟测试只验证 DB 层，不需要 API key：

```bash
python scripts/smoke_test.py
```

它会检查：11 张表能否解析、`Artist + Album` 外键是否连通、合法 SQL 通过、
`DELETE` 被拦截、未知列被识别、只读执行返回 3 行。

## 与参考实现的差异

- 包名从 `sql_agent` 保留，但放在 `作业二/` 下独立运行；默认数据库为
  `作业二/data/chinook.db`（附下载脚本），不再依赖 `D:\AIwork\ref\chinook.db`。
- 默认模型改为 `deepseek-chat`（参考实现为 `deepseek-flash`，可用环境变量覆盖）。
- 链采用 `@lru_cache` 工厂延迟构建，缺少 API key 时仍可导入模块和跑 DB 测试。
- LangSmith 追踪通过环境变量开启，替代 Agents SDK 的 `trace()`。
- 结构化输出方式可配置，便于适配不同兼容端点。

## 已知限制

- 仅支持 SQLite，且一次只查一个数据库文件。
- 仍是无记忆的单轮查询（与参考实现一致），不支持多轮追问。
- 表选择依赖模型能力；目录很大时可进一步做向量检索或分层选表。
- 中文问题效果取决于所选模型，建议使用支持工具调用的模型。
