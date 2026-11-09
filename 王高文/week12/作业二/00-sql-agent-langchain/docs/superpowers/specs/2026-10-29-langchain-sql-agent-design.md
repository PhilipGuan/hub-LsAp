# LangChain SQL Agent 复刻设计规格

日期：2026-10-29

## 目标与范围

在现有 `00-sql-agent` 同级建立独立目录 `00-sql-agent-langchain`，用 LangChain 重现现有 Chinook SQL Agent 的行为。原项目保持不变，新项目提供独立 Python 包与 CLI。

本次覆盖原有单库、单轮自然语言查询流程：数据库解析、表选择、SQL 生成、SQL 校验、SQL 修复、只读执行、结果汇总。不增加多轮对话、写操作或自主规划。

## 架构

- 用 `ChatOpenAI` 连接当前 OpenAI-compatible API。通过 LangChain 的结构化输出能力，将四个角色分别映射为表选择、SQL 生成、SQL 修复、结果汇总链；各角色使用 Pydantic 输出模型。
- 用 `SQLAgentWorkflow` 的 Python 状态机控制调用顺序、表选择重试、共享修复上限和结果降级；模型不持有数据库执行工具。
- 复用现有确定性数据库组件的契约：SchemaCatalog 提供表目录、字段统计、外键关系和样例；SQLValidator 在安全门禁通过后只用只读连接执行 `EXPLAIN`；SQLExecutor 通过只读连接执行，并限制查询时长、返回行数和单元格长度。
- 保留现有空结果答案、汇总失败时的表格降级和 JSON CLI 输出。

## 配置与兼容

- 初始模型配置沿用当前代码：主模型和汇总模型默认 `deepseek-flash`，默认兼容端点为 `https://api.deepseek.com`。
- API Key 从 `OPENAI_API_KEY` 读取；兼容端点可用 `OPENAI_BASE_URL` 覆盖。保留现有 SQL Agent 环境变量配置项。
- 将 Chinook fixture 复制到新项目的 `ref/chinook.db`，新项目默认使用自身 fixture。
- 新项目入口为 `python -m sql_agent_langchain`，安装脚本为 `sql-agent-langchain`。
- 依赖使用 LangChain、LangChain OpenAI integration、Pydantic 与 SQLAlchemy；不使用 LangChain SQL 执行工具，以保留当前安全边界。

## 文件结构

```text
00-sql-agent-langchain/
  pyproject.toml
  README.md
  ref/chinook.db
  src/sql_agent_langchain/
    __init__.py
    __main__.py
    cli.py
    config.py
    logging_config.py
    models.py
    workflow.py
    agents/
      __init__.py
      table_selector.py
      sql_generator.py
      sql_rewriter.py
      result_summarizer.py
    db/
      __init__.py
      catalog.py
      validator.py
      executor.py
  docs/superpowers/specs/2026-10-29-langchain-sql-agent-design.md
```

## 关键行为约束

- 用户问题放在明确的数据边界内传入提示词；表名和列名只取自 catalog。
- 安全校验先于 SQL 编译；只接受单条 SELECT/WITH 查询，拒绝危险关键词；编译使用 EXPLAIN，禁止通过校验器执行 SQL。
- SQL 执行只读，默认最多返回 200 行，汇总最多接收 50 行；单元格截断与 5 秒超时沿用原配置。
- 无效表选择最多反馈重试一次。SQL 校验或运行失败共用最多 3 次修复。
- 空结果不触发修复或模型推测；汇总失败时由代码生成 Markdown 表格降级结果。
- 注释、提示词和面向用户的文本保持中文；日志输出到 stderr，最终 JSON 输出到 stdout。

