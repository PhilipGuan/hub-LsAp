# LangChain SQL Agent 复刻实现计划

> **面向 AI 代理的工作者：** 本计划由当前会话内联执行；不创建子代理或提交代码。

**目标：** 在 `00-sql-agent` 同级创建独立的 `00-sql-agent-langchain` Python 项目，用 LangChain 重现现有 Chinook SQL Agent。

**架构：** 复制并适配确定性数据库层，使用 LangChain 的 `ChatOpenAI`、`ChatPromptTemplate` 与 Pydantic 结构化输出实现四个模型角色。Python 工作流保持对调用顺序、只读校验/执行和最多三次修复的控制。

**技术栈：** Python 3.12、LangChain、langchain-openai、Pydantic、SQLAlchemy、SQLite。

---

## 文件清单

- 创建 `pyproject.toml`：独立依赖、包发现配置和 `sql-agent-langchain` 命令。
- 创建 `README.md`：安装、环境变量、CLI 使用方式和安全行为说明。
- 创建 `ref/chinook.db`：从原项目复制只读 Chinook fixture。
- 创建 `src/sql_agent_langchain/{__init__,__main__,cli,client,config,logging_config,models,workflow}.py`：新包配置、入口、数据契约、日志与状态机。
- 创建 `src/sql_agent_langchain/agents/{__init__,table_selector,sql_generator,sql_rewriter,result_summarizer}.py`：四个 LangChain 结构化输出链。
- 创建 `src/sql_agent_langchain/db/{__init__,catalog,validator,executor}.py`：复用数据库目录、只读 SQL 校验和执行实现，调整包路径。
- 保存本规格与本计划在 `docs/superpowers/specs/`、`docs/superpowers/plans/`。

## 任务 1：搭建独立 Python 包

- [x] 写入 `pyproject.toml`，包名为 `chinook-sql-agent-langchain`，命令入口指向 `sql_agent_langchain.cli:main`。
- [x] 声明 `langchain>=1.0,<2`、`langchain-openai>=1.0,<2`、`pydantic>=2`、`sqlalchemy>=2`。
- [x] 创建包入口和运行配置，沿用当前配置默认值：`deepseek-flash`、`https://api.deepseek.com`、修复上限 3、结果行数 200、汇总行数 50、单元格 2000 字符、超时 5 秒。
- [x] 从环境读取 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`SQL_AGENT_DB` 和现有 `SQL_AGENT_*` 限制项；相对数据库路径以新项目根目录解析。
- [x] 复制 Chinook fixture 到新项目 `ref/chinook.db`；不改动原项目文件。

## 任务 2：迁移确定性数据库层与数据契约

- [x] 迁移 `models.py` 中的 `TableSelection`、`SQLDraft`、`ErrorCode`、`ValidationResult`、`ExecutionResult`、`SQLAttempt`、`FinalAnswer`、`QueryState`、`WorkflowOutput`。
- [x] 迁移 `db/catalog.py` 的 schema 解析、统计、样例、目录/详情渲染、外键连通性检查和进程级缓存。
- [x] 迁移 `db/validator.py` 的注释/字符串屏蔽、单语句门禁、SELECT/WITH 白名单、危险关键词拒绝和只读 `EXPLAIN`。
- [x] 迁移 `db/executor.py` 的只读 URI、查询超时、行数上限、单元格截断与错误码映射。
- [x] 确保目标包的相对导入全部以 `sql_agent_langchain` 为根，且 DB 连接不使用可写模式。

## 任务 3：实现 LangChain 结构化输出链

- [x] 在每个 Agent 模块使用 `ChatPromptTemplate.from_messages` 建立系统提示与用户数据输入。
- [x] 从配置创建 `ChatOpenAI(model=..., base_url=..., api_key=...)`；表选择、SQL 生成、SQL 修复使用主模型，结果汇总使用汇总模型。
- [x] 使用 `with_structured_output(TableSelection)`、`with_structured_output(SQLDraft)`、`with_structured_output(FinalAnswer)` 保持 Pydantic 契约。
- [x] 仅导出四个 chain builder / runnable；Agent 输入、输出和职责与原项目一致，链上不绑定 SQL 执行工具。
- [x] 对所有用户问题在提示词中标明数据边界，并保留只使用 schema 字段、不臆造连接关系的中文规则。

## 任务 4：移植 Python 状态机和 CLI

- [x] 创建 `SQLAgentWorkflow`：空问题快速返回；选表对非法或不连通结果反馈一次；成功后仅向生成/修复链提供已选 schema。
- [x] 校验先于执行；校验或运行失败共用最多 3 次 SQL 修复；修复 Agent 返回 `cannot_fix` 时停止。
- [x] 空结果直接返回确定性中文答案；汇总失败时由代码生成 Markdown 表格降级；最终 `WorkflowOutput` 由代码组装。
- [x] 复制并适配日志配置与 CLI：支持位置参数/标准输入、`--log-level`、API Key 缺失提示、stderr 日志和 stdout JSON。
- [x] 创建 `README.md`，记录 `pip install -e .`、设置 `OPENAI_API_KEY`、可选 `OPENAI_BASE_URL` 与 `python -m sql_agent_langchain "总共有多少张专辑？"`。

## 任务 5：静态检查与交付

- [x] 使用不落盘的 AST 解析检查 17 个 Python 文件语法。
- [ ] Ruff 检查未能执行：当前环境没有安装 Ruff。
- [x] 检查新项目文件清单、README 命令、默认 DB 路径与原项目差异范围。
- [x] 不调用真实模型 API；运行期查询需要用户配置可用的 API Key。



