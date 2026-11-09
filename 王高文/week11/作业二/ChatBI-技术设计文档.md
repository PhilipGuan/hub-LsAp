# ChatBI 技术设计文档

整理日期：2026-10-22｜状态：讨论稿

## 选型原则

复用现有数仓、身份认证和建模工具；模型接入保持可替换；模型生成结构化意图，查询构造和执行由后端控制。首版采用清晰分层的单体后端。若团队以 Java 为主，可保留 Spring 服务，将分析编排作为独立组件。

## 推荐技术栈

| 层 | 建议 | 说明 |
|---|---|---|
| 前端 | Next.js + TypeScript | 对话与管理界面。[Next.js](https://nextjs.org/docs) |
| API/分析服务 | Python + FastAPI | 对话编排、语义解析和查询 API。[FastAPI](https://fastapi.tiangolo.com/) |
| 模型接入 | 官方 SDK + 统一适配层 | 要求结构化意图或工具调用。[OpenAI API 参考](https://platform.openai.com/docs/api-reference/chat/object) |
| 流程编排 | 首版显式服务流程；复杂状态再评估 LangGraph | [LangGraph 文档](https://langchain-ai.github.io/langgraph/) |
| 语义层 | 已有 dbt 优先复用 dbt Semantic Layer；也可评估 Cube | [dbt](https://docs.getdbt.com/docs/build/semantic-models) · [Cube](https://cube.dev/articles/what-is-a-semantic-layer) |
| SQL 检查 | SQLGlot | 解析、检查和转换 SQL。[文档](https://sqlglot.com/) |
| 数据连接 | SQLAlchemy 或数仓官方驱动 | 数据库连接和查询执行。[SQLAlchemy](https://docs.sqlalchemy.org/en/21/core/index.html) |
| 应用元数据 | PostgreSQL | 用户、角色、语义配置、会话元数据和审计；分析明细留在数仓。 |
| 部署 | Docker + 企业现有平台 | 复用已有部署、密钥管理和监控规范。 |

## 服务边界与查询链路

前端只调用 ChatBI API，不直连数仓。API 负责用户上下文、意图解析、权限校验、查询流程和结果格式化。语义层提供指标、维度、关系、别名和时间口径。数仓访问层使用只读账号并统一控制超时和结果规模。

查询流程：获取用户身份 → 解析指标/时间/维度/筛选 → 匹配语义并处理歧义 → 形成结构化查询计划 → 校验字段、操作、数据范围和成本 → 后端生成 SQL → 检查查询结构 → 只读执行 → 格式化结果与审计信息。

SQL 解析本身不是完整安全边界，必须结合权限白名单、只读账号和资源限制。

## 模型职责

模型负责自然语言理解、结构化意图、澄清和结果摘要。模型不持有数据库凭据，不决定权威指标口径，也不绕过授权。优先使用“结构化查询计划 → 后端生成 SQL”；即便采用 SQL 生成，也必须经过语义白名单、AST 校验及只读限制。

## 安全与质量

复用 SSO/RBAC；凭据只保存在服务端；限制 schema、表、字段、指标和行范围；设置超时、行数或扫描量上限；记录用户、语义版本、意图匹配、查询状态、耗时和错误类型。数据库支持时可增加行级安全策略，例如 [PostgreSQL Row Security](https://www.postgresql.org/docs/current/ddl-rowsecurity.html)。

用人工确认的问题集评估语义映射、结果口径、权限边界和失败处理。向量检索仅在词典/元数据规模需要时增加；Redis 和复杂 Agent 框架也按明确需求引入。
