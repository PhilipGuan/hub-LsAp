# Chinook SQL Agent

使用自然语言查询数据库，默认使用 SQLite 数据库 `chinook.db`，实现“数据库结构解析 → 表选择 → SQL 生成 → SQL 校验 → 失败改写 → SQL 执行 → 结果汇总”的完整流程。前端使用 Streamlit 提供简单问答页面，后端使用 FastAPI 提供 SSE 流式问答接口。Python 运行环境使用现有 Conda 环境 `badou_env3.12`。

已实现 FastAPI SSE 接口、LangChain 工作流和 Streamlit 问答页面。使用前请在 `config.py` 中填写自己的 `LLM_API_KEY`。不使用 `.env`、`docs`、`examples`、`tests` 目录，配置直接定义在 Python 文件中。

这是一个演示项目，配置模型密钥后即可通过问答页面体验完整流程。

核心参考：`../课程/第11周-ChatBI数据智能问答/Week11/04_SQL-Code-Agent-Demo/code-agent.ipynb`。

## 一、项目结构

```text
sql-agent-langchain/
├── README.md
├── main.py                       # FastAPI 应用与后端启动入口
├── __init__.py                   # 业务包入口
├── config.py                     # 数据库路径和模型连接配置
├── chinook.db                    # 项目使用的 SQLite 数据库
├── requirements.txt              # 运行依赖
├── api/                          # 接口层
│   ├── __init__.py
│   └── chat.py                   # SSE 问答接口和健康检查接口
├── services/                     # 服务层
│   ├── __init__.py
│   ├── chat_service.py           # 七阶段编排、状态管理和改写循环
│   ├── database_service.py       # 数据库连接、结构解析、SQL 校验、预执行和查询执行
│   └── llm_service.py            # LangChain 模型适配、链运行与响应处理
├── models/                       # 数据模型与接口出入参
│   ├── __init__.py
│   ├── chat.py                   # ChatRequest、ChatResponse、ChatEvent
│   └── workflow.py               # 结构、选表、SQL、校验及执行结果模型
├── agent/                   # 业务智能体层，使用 LangChain 链
│   ├── __init__.py
│   ├── table_agent.py            # 根据问题选择表
│   ├── sql_agent.py              # 生成 SQL、根据报错改写 SQL
│   └── summary_agent.py          # 根据真实查询结果组织回答
└── frontend/                     # Streamlit 前端
    ├── app.py                    # 问答页面、后端请求和结果展示
    └── sse.py                    # SSE 事件解析
```

业务智能体使用 `agent/` 包，通过普通导入加载。PyCharm 中将项目根目录标记为 Sources Root，并选择 `badou_env3.12` 解释器。启动时使用 `python main.py` 或 `python -m uvicorn main:app`。

提示词直接放在对应 Agent 文件中；SQL 生成和改写共用 SQL Agent，通过不同输入区分任务。各智能体使用 LangChain 的 `ChatPromptTemplate` 和 `ChatOpenAI` 组成 Runnable 链；工作流顺序和改写循环由服务层显式控制。首版不增加自定义 Agent 基类或额外的存储服务。

## 二、技术与调用关系

| 部分 | 方案 | 职责 |
| --- | --- | --- |
| 前端 | Streamlit、requests、pandas | POST 请求订阅 SSE，实时展示阶段进度、回答与数据 |
| 接口 | FastAPI、Uvicorn、StreamingResponse | 请求校验、SSE 事件输出、后端启动 |
| 数据模型 | Pydantic | 接口出入参和工作流结构化数据 |
| 数据库 | SQLAlchemy 2.0 Engine、ORM Session、Inspector | 统一连接、结构反射、参数化查询与方言适配 |
| SQL 解析 | sqlglot | 根据实际数据源方言解析 SQL 语法树 |
| 模型调用 | LangChain（`langchain-core`、`langchain-openai`）、HTTPX | 使用提示词与模型组成链，通过 `ainvoke` 和 `astream` 运行 |

```text
frontend/app.py
    → POST /api/chat
    → api/chat.py
    → services/chat_service.py
        → database_service：解析数据库
        → table_agent：选择表
        → sql_agent：生成 SQL
        → database_service.check_sql：校验与预执行
            → 失败：sql_agent 改写 → 再次 check_sql
        → database_service：执行通过校验的 SQL
        → summary_agent：汇总结果
    → SSE：step / result / answer_delta / error / done
    → Streamlit 实时更新进度、回答、结果表格和 SQL
```

前端通过 HTTP POST 提交问题，后端以 `text/event-stream` 持续推送事件。结构解析、选表、SQL 生成、校验、改写和执行发送阶段进度；查询成功后先发送结果数据，再逐段推送汇总回答，最后发送终态快照。

### LangChain 接入约定

- `table_agent.py` 定义选表链，输出 `TableSelection`。
- `sql_agent.py` 定义 SQL 链，负责生成与改写，输出 `SQLCandidate`。
- `summary_agent.py` 定义汇总链，输出自然语言文本，并过滤思考标签。
- `llm_service.py` 通过 `ChatPromptTemplate | ChatOpenAI` 构建链。选表、SQL 生成及改写使用 `with_structured_output(..., method="json_mode")` 和 `ainvoke()`，由 Pydantic 校验输出；JSON Schema 直接写入系统提示词，兼容当前模型的 JSON 对象模式。
- 汇总使用 `ChatPromptTemplate | ChatOpenAI | StrOutputParser`，通过 `astream()` 获取文本增量，再转换成业务 `answer_delta` 事件。取消或超时会关闭流，请求结束后关闭模型客户端。
- `chat_service.py` 保留七阶段顺序与最多 3 次 SQL 改写；数据库校验和执行由服务层直接调用，保证每个 SQL 必经校验。每次链调用只请求一次模型，超时 60 秒，不自动重试。
- 使用 `config.py` 中的硅基流动兼容接口和 DeepSeek 模型配置，保留 `enable_thinking` 参数。业务模型调用统一使用 `langchain-openai` 的 `ChatOpenAI`，项目不直接导入或调用原生 `openai` SDK，也不使用 OpenAI Agents SDK。`openai` 包由 `langchain-openai` 作为传递依赖安装，不在项目依赖中单独声明。HTTPX 仅用于连接生命周期管理。
- 格式不合规、认证失败、超时和模型调用失败继续转换为现有结构化业务错误；汇总失败保留查询结果并降级回答。

依据 [LangChain ChatOpenAI 官方文档](https://docs.langchain.com/oss/python/integrations/chat/openai)。

## 三、完整工作流

### 1. 运行时解析数据库结构

每次问答开始时，通过 SQLAlchemy Inspector 和 Table 反射读取当前数据库结构。底层由数据库方言生成元数据 SQL，保存在本次请求的内存中，不维护独立结构文件。

数据库解析包括：

- 使用 `inspect(connection).get_table_names()` 获取默认 schema 的业务表，排除 SQLite 内部表。
- 通过 `Table(..., autoload_with=connection)` 反射字段、类型、可空性和主键；使用 `CreateTable` 编译结构描述。
- 通过 Inspector 读取外键，构建默认 schema 内的表关联关系。
- 自动获取数据库方言与默认 schema，传递给 SQL 生成、改写和校验。
- 为选表 Agent 提供所有表的结构摘要，为 SQL Agent 提供选中表的完整结构。

数据库通过 `config.py` 的 `DATABASE_URL` 连接，默认指向项目根目录下的 `chinook.db`。SQLite 文件强制使用只读 URI 打开；文件不存在时返回明确错误，不自动创建空数据库。数据库连接和 ORM Session 均在执行该次操作的工作线程内创建并关闭。

### 2. Table 选择 Agent

输入：用户问题、全部表的结构摘要、外键关系。

输出：`TableSelection(tables, reason)`，其中 `reason` 仅包含简短选表依据。

服务层确认表名真实存在，并根据外键关系补齐必要的中间表。例如“销售收入最高的艺术家”需要 `artists`、`albums`、`tracks`、`invoice_items`。

如果问题缺少必要统计口径，返回需要澄清的说明；不虚构表、字段或业务含义。

### 3. SQL 生成 Agent

输入：用户问题、选中的表及字段、外键关系、实际数据库方言和业务口径。

输出：`SQLCandidate(sql, params)`，通过 LangChain JSON 模式和 Pydantic 校验得到；SQL 和绑定参数分开。

生成规则：

- 仅生成单条只读查询，允许 `SELECT` 和只读 `WITH ... SELECT`。
- 使用真实表名、字段名和明确的关联条件。
- 文本筛选值使用命名参数，例如 `Country = :country`。
- Top N 明确排序与数量；必要时增加稳定排序键。
- 不生成 Python 程序，不调用 `exec()` 或 `eval()`。

### 4. SQL 校验：database_service.check_sql

统一入口为：

```python
database_service.check_sql(candidate, schema, selected_tables) -> SQLCheckResult
```

该函数位于 `services/database_service.py`，内部完成静态校验和数据库预执行，与正式执行共用连接管理、只读约束和超时控制。

| 顺序 | 校验内容 | 失败处理 |
| --- | --- | --- |
| 1 | SQL 非空、参数结构与命名占位符匹配 | 进入改写 |
| 2 | 使用 sqlglot 按当前数据库方言解析，确认只有一条只读查询 | 语法错误改写；写操作直接拒绝 |
| 3 | 检查真实基表属于选中集合，正确识别别名、CTE 和子查询 | 结构错误进入改写 |
| 4 | 通过 ORM Session 绑定参数执行 SQLite 的 `EXPLAIN QUERY PLAN` 或 PostgreSQL/MySQL 的 `EXPLAIN`，检查能否编译 | 进入改写 |
| 5 | 在只读连接中实际预执行 SQL，并受时间与取数限制约束 | 可修复执行错误进入改写 |

预执行使用 `execute` 并有界读取结果，覆盖执行及取数期间的错误；结果仅用于校验，随后关闭游标。第 6 步再正式执行并获取用于回答的数据。此设计有意保留“预执行 → 正式执行”两个阶段，会产生两次数据库查询。

SQLAlchemy 统一使用 `Session.execute(text(...), params)` 执行动态 SQL，不为每张表编写固定 ORM 实体。SQLite 保留 `mode=ro`、`query_only` 和授权器；PostgreSQL 使用只读事务及 `statement_timeout`；MySQL 使用只读会话和 `MAX_EXECUTION_TIME`。静态校验拒绝写入、DDL、系统表和写锁，远程数据库额外限制可调用函数。远程数据源应使用仅有业务表读取权限的账号。

校验通过仅表示查询符合约束且能够运行，业务口径仍由生成规则和结果验收保证。

### 5. SQL 改写与循环校验

将用户描述中的“保持信息”按上下文理解为“报错信息”。改写输入为：**原用户问题 + 当前 SQL 和参数 + 校验或预执行报错信息 + 相关数据库结构**。

流程为：

```text
check_sql
  ├─ 通过 → 正式执行
  └─ 可修复失败 → 改写 SQL → check_sql
                            └─ 再失败 → 继续改写
```

默认最多改写 3 次，即初始候选加 3 次修订。循环持续到校验通过或达到上限，避免请求无限等待。每次改写都经过完整校验，不能跳过预执行。

- 语法、字段、关联、参数等问题可以改写。
- 需要补选表时重新调用选表 Agent，仍占用同一改写预算。
- 相同 SQL、参数和报错重复出现时提前结束。
- 权限违规、数据库不可用、执行超时、模型认证失败直接结束，不通过改写放宽限制。
- 达到上限后返回最后失败阶段和报错，不生成业务答案。

### 6. 正式执行 SQL

校验通过后，使用与预执行相同的只读策略执行 SQL，并绑定参数。

返回 `QueryResult`：列名、结果行、返回行数、耗时和截断标记。默认最多返回 100 行，通过读取第 101 行判断是否截断；空集为成功结果，不自动触发 SQL 改写。

SQLite 预执行和正式执行各有 5 秒预算，进度回调同时检查超时和取消标记。PostgreSQL/MySQL 使用服务端每条语句 5 秒限制；远程查询断连后可能等到服务端超时才结束，不能保证立即取消。返回行数限制不能替代执行超时限制。Decimal、日期时间、UUID 和二进制结果会转换为可序列化值。

若正式执行发生可修复错误，也进入同一改写循环，共享剩余预算；新 SQL 必须再次通过 `check_sql`。

### 7. 结果汇总 Agent

输入：用户问题、最终 SQL、列名、实际结果和截断标记。

输出：中文自然语言回答，通过 SSE 的 `answer_delta` 逐段发送。

- 结论必须来自实际查询结果，说明必要的统计范围和指标含义。
- 空结果明确说明没有匹配记录；`NULL` 不擅自解释为零。
- 数据被截断时说明仅展示部分结果，不利用部分行推断全量统计。
- 汇总失败时保留 SQL 和数据，发送非致命 `error` 事件；随后用 `done` 中的完整 `answer` 替换已显示的部分回答，并设置 `summary_degraded=true`。
- 数据库字段和查询结果作为数据处理，不作为模型指令。

## 四、模块接口设计

| 模块 | 主要接口 | 说明 |
| --- | --- | --- |
| `database_service` | `parse_schema() -> DatabaseSchema` | 运行时读取结构 |
| `database_service` | `execute_sql(candidate, allowed_tables) -> QueryResult` | 提供预执行和正式执行共用的只读执行能力 |
| `table_agent` | `select_tables(llm, question, schema, feedback=None) -> TableSelection` | 根据问题选择表 |
| `sql_agent` | `generate_sql(llm, question, schema, selection) -> SQLCandidate` | 生成候选 SQL |
| `database_service` | `check_sql(candidate, schema, selected_tables) -> SQLCheckResult` | 静态检查及预执行 |
| `sql_agent` | `rewrite_sql(llm, question, candidate, error, schema, selection) -> SQLCandidate` | 根据错误修订 SQL |
| `summary_agent` | `stream_summary(llm, question, candidate, result) -> AsyncIterator[str]` | 基于结果输出回答文本增量 |
| `chat_service` | `stream_ask(request) -> AsyncIterator[ChatEvent]` | 编排全流程、管理改写、逐步产生业务事件 |
| `llm_service` | `async run_agent(agent, input_data, output_type)` | 调用链的 `ainvoke`，读取结构化输出并处理模型异常 |
| `llm_service` | `stream_agent(agent, input_data) -> AsyncIterator[str]` | 使用链的 `astream` 消费文本增量 |

Agent 不管理数据库连接或重试循环；服务层负责连接生命周期、校验、执行与错误分流。

## 五、数据模型与 API

### 内部模型：models/workflow.py

| 模型 | 主要字段 |
| --- | --- |
| `ColumnSchema` | `name`、`data_type`、`nullable`、`primary_key` |
| `ForeignKeySchema` | `column`、`target_table`、`target_column` |
| `TableSchema` | `name`、`ddl`、`columns`、`foreign_keys` |
| `DatabaseSchema` | `dialect`、`schema_name`、`tables` |
| `TableSelection` | `tables`、`reason` |
| `SQLCandidate` | `sql`、`params` |
| `WorkflowError` | `stage`、`code`、`message`、`retryable` |
| `SQLCheckResult` | `passed`、`error` |
| `QueryResult` | `columns`、`rows`、`returned_rows`、`truncated`、`elapsed_ms` |
| `WorkflowStep` | `stage`、`status`、`attempt`、`message`、`elapsed_ms` |

`params` 为参数名到标量值的字典；`rows` 使用二维数组，与 `columns` 配套，避免同名结果列被字典覆盖。

### SSE 问答接口：POST /api/chat

请求模型 `ChatRequest`：

```json
{
  "question": "数据库一共有多少首曲目？"
}
```

`POST /api/chat` 为 SSE 接口，不提供普通 JSON 问答响应。

`question` 去除首尾空白后不能为空，最大 2000 字符。首版每次请求独立处理；前端保留聊天展示记录，不将历史对话隐式带入 SQL 生成。

请求头使用 `Content-Type: application/json` 和 `Accept: text/event-stream`。响应由 FastAPI `StreamingResponse` 输出，设置 `Content-Type: text/event-stream; charset=utf-8`、`Cache-Control: no-cache`；经过代理时关闭代理缓冲（如 `X-Accel-Buffering: no`）。

接口返回 SSE 事件流，`ChatResponse` 作为 `done` 事件的完整终态快照，不再作为普通 JSON 响应体返回。`models/chat.py` 中定义 `ChatEvent`，事件类型与 `data` 模型一一对应。

| event | data 内容 | 发送时机 |
| --- | --- | --- |
| `step` | `stage`、`status`、`attempt`、`message`、`elapsed_ms` | 阶段开始、完成及改写进度；可修复失败仍作为阶段事件 |
| `result` | `selected_tables`、`sql`、`params`、`columns`、`rows`、`returned_rows`、`truncated` | 正式执行成功后，在汇总开始前发送一次 |
| `answer_delta` | `delta` | 汇总模型产生可展示文本时逐段发送 |
| `error` | `stage`、`code`、`message`、`fatal` | 不可恢复失败或汇总降级；不暴露密钥和内部堆栈 |
| `done` | 完整 `ChatResponse` | 成功、需要澄清、失败的统一终态，连接正常时恰好发送一次 |

业务事件使用 `event: 类型` 和 `data: JSON`，以空行结束。JSON 通过序列化产生，文本换行在 JSON 字符串中转义；网络分块不作为事件边界。格式示意：

```text
event: step
data: {"stage":"table_selection","status":"running","attempt":0,"message":"正在选择数据表","elapsed_ms":0}

event: result
data: {"selected_tables":["tracks"],"sql":"SELECT COUNT(*) AS track_count FROM tracks","params":{},"columns":["track_count"],"rows":[[3503]],"returned_rows":1,"truncated":false}

event: answer_delta
data: {"delta":"数据库共有 "}

event: answer_delta
data: {"delta":"3503 首曲目。"}

```

随后发送 `event: done`，其 `data` 为以下结构（数据以实际数据库为准）：

```json
{
  "status": "success",
  "answer": "数据库共有 3503 首曲目。",
  "selected_tables": ["tracks"],
  "sql": "SELECT COUNT(*) AS track_count FROM tracks",
  "params": {},
  "columns": ["track_count"],
  "rows": [[3503]],
  "returned_rows": 1,
  "truncated": false,
  "rewrite_count": 0,
  "summary_degraded": false,
  "steps": [],
  "error": null
}
```

`steps` 在实际运行中记录阶段、状态和耗时，上例省略其内容。`status` 支持 `success`、`needs_clarification`、`failed`。失败时保留已有 SQL、选表和步骤信息，`error` 返回结构化错误；未产生的数据使用空列表或 `null`，不伪造结果。

流建立前的请求字段错误返回普通 HTTP 422，启动响应前的内部异常可返回 HTTP 500。响应头发出后不能改变 HTTP 状态码：业务或内部异常转换为 `error`，随后发送 `done(status="failed")` 并结束；汇总降级发送 `error(fatal=false)` 后以 `done(status="success", summary_degraded=true)` 结束。

连接管理约定：

- 无业务事件时每 15 秒发送 SSE 注释心跳 `: ping`，由接口层独立维护；长时间模型等待不阻止心跳。
- 数据库同步操作在工作线程执行，连接在该线程内创建、使用和关闭，避免阻塞事件循环和跨线程复用 SQLite 连接。
- 检测客户端断开或生成器取消后，取消模型流和工作流任务；数据库进度回调检查取消标记并配合超时退出，最终释放连接和后台任务。
- 客户端收到 `done` 才认为请求完成；未收到 `done` 就断流时标记为中断，保留已收到的数据与部分回答，不显示为成功。
- 首版不提供断点续传或自动重连；重试由用户重新提交，避免重复执行和重复拼接回答。

### 健康检查：GET /api/health

返回后端进程是否可用，不调用模型。数据库不可访问等业务配置错误在问答接口中明确返回。

## 六、Streamlit 简单问答页面

页面仅保留以下元素：

1. 标题“Chinook 数据智能问答”。
2. 使用 `st.chat_input` 输入问题，`st.chat_message` 展示问答。
3. 根据 `step` 实时显示当前阶段、校验结果与改写进度。
4. 收到 `result` 后通过 `st.dataframe` 展示查询数据；收到 `answer_delta` 时追加文本并刷新回答占位区。
5. 使用折叠区展示选中的表、最终 SQL、绑定参数、改写次数和执行步骤。
6. 失败时展示错误说明；需要澄清时引导用户补充完整问题；截断时提示仅显示前若干行。

前端使用 `requests.post(..., stream=True)` 发送 JSON 请求，先检查 HTTP 状态码与响应类型，再按 SSE 空行边界组装事件，处理 `event:`、多行 `data:` 和注释心跳，JSON 完整后再解析。显式使用 UTF-8 解码，及时消费事件，避免 requests 默认分块缓冲导致短增量延迟展示。POST 请求无需浏览器 EventSource。

使用 `st.empty()` 占位区增量刷新回答。`done.answer` 为最终权威文本，收到后替换增量缓存，避免重复拼接，并处理汇总降级场景。

聊天记录保存在 `st.session_state`，保存回答时一并保存终态、该次数据与 SQL，页面重绘后仍可展开。支持“清空对话”；首版不增加登录、文件上传、图表或数据库管理页面。

## 七、配置与启动设计

使用现有 Conda 环境 `badou_env3.12` 运行后端、前端和安装依赖，不新建虚拟环境。

`config.py` 仅保留项目路径、数据库路径和模型连接配置：

| 配置 | 约定 |
| --- | --- |
| `PROJECT_ROOT` | 项目根目录 |
| `DATABASE_URL` | SQLAlchemy 数据库 URL，默认 `sqlite+pysqlite:///chinook.db`；SQLite 相对路径按项目根目录解析 |
| `LLM_API_KEY` | 用户本地填写自己的模型密钥 |
| `LLM_BASE_URL` | `https://api.siliconflow.cn/v1`，硅基流动兼容接口地址 |
| `LLM_MODEL` | `deepseek-ai/DeepSeek-V3` |
| `LLM_ENABLE_THINKING` | `False`，通过 `enable_thinking` 传给模型服务 |

其余参数直接写在对应实现中，不再作为配置项：

- 模型请求超时 60 秒，每次链调用请求一次模型，结构化输出固定采用 JSON 模式加 Pydantic 校验。
- SQL 最多改写 3 次，默认最多返回 100 行，预执行和正式执行各自限时 5 秒。
- 后端监听 `127.0.0.1:8000`；Streamlit 请求此地址，连接超时 5 秒、读取超时 45 秒。
- SSE 空闲心跳间隔 15 秒。读取超时用于检测连接停滞，不是整个工作流的总时长；断连后执行取消和资源清理。

切换数据源时修改 `DATABASE_URL`，数据库服务会自动反射实际结构并选用相应方言：

```python
# 默认 SQLite，无需额外驱动
DATABASE_URL = "sqlite+pysqlite:///chinook.db"
# PostgreSQL：在当前 Conda 环境安装 psycopg[binary]
# DATABASE_URL = "postgresql+psycopg://readonly_user:password@127.0.0.1:5432/database"
# MySQL 8+：在当前 Conda 环境安装 pymysql
# DATABASE_URL = "mysql+pymysql://readonly_user:password@127.0.0.1:3306/database?charset=utf8mb4"
```

当前开放默认 schema 内的表，未实现跨 schema 查询；切换后重启后端。SQL 生成提示词依据实际方言和结构工作，Chinook 口径只用于对应表和字段存在且含义一致的情况。

已验证 SQLAlchemy 对本地 Chinook 及另一份独立 SQLite 数据库的反射和执行，以及 PostgreSQL/MySQL 的静态 SQL 校验与参数编译；未连接真实 PostgreSQL/MySQL 实例验收。

先在 `config.py` 填写 `LLM_API_KEY`，确认模型名称和服务地址。然后在项目根目录启动，后端与前端各占一个终端：

```bash
conda activate badou_env3.12
python -m pip install -r requirements.txt
python main.py
```

```bash
conda activate badou_env3.12
python -m streamlit run frontend/app.py
```

`main.py` 创建 FastAPI 应用、挂载路由，并在直接运行时通过 Uvicorn 启动。Streamlit 与后端独立运行；前端默认端口 8501。

启动后访问 Streamlit 页面：[http://localhost:8501](http://localhost:8501)。后端健康检查为 [http://127.0.0.1:8000/api/health](http://127.0.0.1:8000/api/health)。未配置密钥时，问答会通过 SSE 返回配置错误，健康检查仍可用。

## 八、Chinook 业务口径

- 客户累计消费：按客户聚合 `invoices.Total`。
- 曲目、流派和艺术家收入：聚合 `invoice_items.UnitPrice * invoice_items.Quantity`。
- 销量：聚合 `invoice_items.Quantity`；曲目总数：统计 `tracks` 行数。
- 发票连接明细后不能直接累加发票头 `Total`，避免一对多关联重复计费。
- 按国家统计销售额默认使用 `invoices.BillingCountry`；用户明确要求客户所在地时使用 `customers.Country`。
- 发票日期使用 `invoices.InvoiceDate`；未指定日期范围时统计数据库全部历史数据。
- 金额显示保留两位小数，不自行假设币种。

## 九、与参考 Notebook 的对应关系

| Notebook 思路 | 本项目实现设计 |
| --- | --- |
| `action()` 串联生成、执行、反思和汇总 | `chat_service.stream_ask()` 编排七阶段流程并输出 SSE 业务事件 |
| `llm()` 统一请求模型 | `llm_service` 通过 LangChain `ainvoke()` 获取结构化结果，通过 `astream()` 输出汇总增量 |
| `_code_gen_prompt` | `sql_agent.generate_sql()` 的 SQL 生成提示词 |
| `_code_reflection_prompt` 接收错误反馈 | `sql_agent.rewrite_sql()` 接收原问题、SQL 和报错 |
| 重试循环 | SQL 校验与改写共享有限预算 |
| `execute()` 执行生成内容 | `database_service.check_sql()` 预执行后，由数据库服务只读正式执行 |
| `_summary_prompt` | `summary_agent.stream_summary()` 基于查询结果流式汇总 |

保留 Notebook 的错误反馈闭环，将生成目标收敛为结构化 SQL，并由程序掌控执行权限、查询限制和错误处理。

## 十、验收

已实现上述模块，依赖固定在 `requirements.txt`。配置模型密钥后，通过问答页面演示完整流程。

自动回归测试使用项目根目录的 `test_*.py`，无需真实模型密钥：

```bash
conda activate badou_env3.12
python -m unittest discover -v
```

`test_llm_service.py` 通过本地 HTTP 模型替身验证真实 LangChain 链调用，并连接项目自带 Chinook 数据库，覆盖结构化输出、SQL 改写、流式汇总、错误映射、取消和汇总降级。现有测试继续覆盖思考标签过滤与前端展示。真实模型服务仍需填写自己的密钥后验收。

通过问答接口和页面进一步验收：

- 曲目总数、客户消费前五名、流派收入前五名、发票日期范围等问题，结果与直接数据库查询一致。
- 错误字段或语法能够触发“改写 → 再校验 → 正式执行”，达到预算后停止。
- 每个候选都必须通过 `database_service.check_sql`，写操作及多语句被拒绝。
- 空结果、查询截断、SQL 超时、模型错误在页面中有明确反馈。
- 汇总失败仍能查看成功查询的数据；失败查询不生成成功结论。
- 前端能够实时展示阶段进度与回答增量，并查看数据和展开 SQL。
- 首条进度事件及时到达；长任务期间有心跳；正常终止仅有一个 `done`。
- 流中错误、汇总中途失败、缺失 `done` 的断流及客户端断开均按约定处理，中文和跨分块事件能够完整解析。
