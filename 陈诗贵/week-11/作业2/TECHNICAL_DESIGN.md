# ChatBI 技术设计文档

> Technical Design · v0.1 · 参考实现方案

**版本：** v0.1（讨论稿）  
**日期：** 2026-10-22  
**项目：** ChatBI 对话式商业智能

## 文档说明

本文给出 ChatBI 的参考技术架构、模块职责、核心数据模型、接口约定、查询安全、模型调用、可观测性、测试与部署原则。由于具体数据库、云环境、模型供应商和并发规模尚未确定，技术选型属于建议方案，需在技术评审后固化。

推荐原则：模块化单体优先、语义层优先、权限确定性执行、只读查询、结构化模型输出、可复现评测。不要让大模型直接持有数据库凭据或自行决定授权范围。

## 1. 技术目标与设计原则

- 正确性：指标与维度从语义目录解析，关键 SQL 和结果经过校验。
- 安全性：权限由服务端策略和数据库权限共同保障，默认只读。
- 可追溯性：每个请求关联用户、会话、语义版本、模型版本、SQL 哈希和执行结果。
- 可替换性：模型供应商、数据库适配器、图表渲染组件通过接口隔离。
- 可测试性：核心业务逻辑不依赖真实 LLM；支持 Mock、固定输出和回归测试。
- 成本控制：限制上下文长度、查询范围、返回行数、执行时间和模型重试次数。
## 2. 参考架构

建议首期采用模块化后端 + Web 前端 + 关系型元数据库的架构。数据分析查询由独立只读连接器访问业务数据源；元数据库保存用户、语义配置、会话和审计信息。

```text
Web UI → API / Auth → Conversation Orchestrator
                         ├─ Intent & Semantic Resolver
                         ├─ Clarification Manager
                         ├─ Query Planner / LLM Adapter
                         ├─ SQL Policy Validator
                         ├─ Query Executor → Read-only Data Source
                         ├─ Result Validator / Chart Spec Builder
                         └─ Audit / Metrics / Feedback

Metadata DB: users, roles, data_sources, semantic_catalog,
             conversations, messages, query_runs, audit_events
```

上述模块可以在同一个后端进程内实现。只有在吞吐、隔离或团队协作确有需要时，再将查询执行、任务队列或模型网关拆分成独立服务。

## 3. 模块职责与接口边界

| 模块 | 输入 | 输出 / 责任 |
| --- | --- | --- |
| API / Auth | HTTP 请求、用户凭证 | 认证身份、权限上下文、请求 ID |
| Conversation Orchestrator | 用户问题、会话 ID | 编排解析、澄清、执行、结果生成的生命周期 |
| Semantic Resolver | 问题、语义目录、会话条件 | 指标 / 维度 / 时间 / 筛选的结构化需求 |
| Clarification Manager | 结构化需求、歧义列表 | 澄清问题或确认后的需求 |
| Query Planner | 已确认需求、语义定义 | 查询计划与候选 SQL |
| SQL Policy Validator | 候选 SQL、用户权限、查询策略 | 通过 / 拒绝及原因码 |
| Query Executor | 已校验 SQL、只读连接 | 结果集、列元数据、执行耗时 |
| Result Validator | 结果集、查询计划 | 一致性检查、截断标志、数据质量提示 |
| Presentation Builder | 已验证结果、问题上下文 | 表格数据、图表规格、受限摘要 |
| Audit / Evaluation | 阶段事件与版本信息 | 审计日志、评测记录、监控指标 |

模块间传递结构化对象，不应依赖难以解析的自由文本。业务错误使用稳定错误码；面向用户的文案由上层映射，避免直接暴露数据库堆栈。

## 4. 请求生命周期

1. 接收请求：验证登录态、会话归属、数据域权限与请求大小，生成 request_id。
1. 读取上下文：加载必要的最近会话内容、已确认条件和语义目录版本；避免无限拼接全部历史。
1. 解析问题：抽取指标、维度、过滤、时间范围、比较条件、排序和图表意图。
1. 语义校验：确认字段存在、指标口径唯一、关联路径有效；对关键歧义返回 clarification_required。
1. 生成查询计划：使用受控语义定义生成逻辑计划，再生成 SQL；不把用户输入直接拼接为 SQL。
1. 执行安全校验：解析 SQL AST 或使用成熟 SQL parser，检查语句类型、表列白名单、函数、Join、限制、权限和资源上限。
1. 执行查询：使用只读数据库身份，设置 statement timeout、最大行数和并发限制。
1. 校验结果：检查列、单位、结果规模、截断状态、空值和计划一致性。
1. 生成呈现：由确定性代码构造表格 / 图表规格；LLM 仅根据已验证结果撰写简短说明。
1. 持久化和返回：保存消息、查询记录、语义 / 模型版本、状态和耗时，返回前端结果。
## 5. 技术选型建议

| 层 | 参考选项 | 选型原则 / 注意事项 |
| --- | --- | --- |
| 前端 | React / Vue + TypeScript | 采用团队熟悉的框架；图表组件与查询逻辑分离 |
| 后端 | Python FastAPI 或现有团队框架 | Python 适合数据与 LLM 生态；不必为技术潮流重写已有系统 |
| 元数据存储 | PostgreSQL / MySQL | 保存配置、会话、审计；与业务查询数据源逻辑隔离 |
| 模型接入 | 统一 LLM Adapter | 支持结构化输出、超时、配额和供应商切换 |
| SQL 解析 | 成熟 SQL parser / AST 工具 | 不能只用正则表达式识别危险 SQL |
| 任务执行 | 同步请求起步；长查询再引入队列 | 按实际响应时间和并发决定 |
| 图表 | 前端图表库 + 服务端 Chart Spec | 图表配置必须由受控结构生成，不执行模型返回的脚本 |
| 监控 | 结构化日志 + metrics + tracing | 敏感值脱敏，指标按阶段拆分 |

这不是唯一可行方案。最终选型需结合现有技术栈、数据源类型、部署环境、合规要求、预算和团队能力。

## 6. 数据模型建议

| 实体 / 表 | 关键字段建议 | 说明 |
| --- | --- | --- |
| users | id, name, status, created_at | 用户身份与状态；认证可复用企业身份系统 |
| roles / user_roles | role_id, user_id, scope | 角色与数据域授权 |
| data_sources | id, name, type, secret_ref, status, policy_json | 凭据引用应指向密钥管理，不应明文存储密码 |
| semantic_metrics | id, name, description, expression, unit, owner, version, status | 指标定义和版本 |
| semantic_dimensions | id, name, description, field_ref, data_type, synonyms | 维度与业务同义词 |
| semantic_relationships | source_ref, target_ref, join_keys, cardinality | 关联关系及基数约束 |
| conversations | id, user_id, title, data_scope, created_at, updated_at | 会话归属与上下文范围 |
| messages | id, conversation_id, role, content_redacted, status, created_at | 用户与系统消息；敏感信息按策略脱敏 |
| query_runs | id, request_id, user_id, semantic_version, model_version, sql_hash, status, duration_ms, row_count, error_code | 查询运行记录；SQL 原文应受控保存或加密 |
| audit_events | id, actor_id, action, resource, decision, request_id, created_at | 管理操作与权限决策审计 |
| feedback | id, message_id, rating, reason_code, comment_redacted, created_at | 结果反馈与质量改进 |

表结构是逻辑模型，不是最终 DDL。实施前需要补充主键策略、外键、索引、分区、数据保留、租户隔离和并发写入要求。

## 7. API 设计草案

### 7.1 创建 / 继续分析

POST /api/v1/chat/query

```json
{
  "conversation_id": "conv_123",
  "data_scope_id": "sales",
  "question": "统计本月各渠道销售额，并按从高到低排序",
  "display_preferences": { "chart": "auto" }
}
```

```json
{
  "request_id": "req_456",
  "status": "completed",
  "conversation_id": "conv_123",
  "analysis": {
    "metrics": ["sales_amount"],
    "dimensions": ["channel"],
    "time_range": { "start": "2026-10-01", "end_exclusive": "2026-11-01", "timezone": "Asia/Tokyo" }
  },
  "result": {
    "columns": [{ "name": "channel", "type": "string" }, { "name": "sales_amount", "type": "number", "unit": "currency" }],
    "rows": [],
    "truncated": false
  },
  "visualization": { "type": "bar", "x": "channel", "y": "sales_amount" },
  "summary": "根据查询结果生成的简短说明",
  "provenance": { "data_scope": "sales", "semantic_version": "v3", "executed_at": "2026-10-22T02:00:00Z" }
}
```

以上为接口形状示例，不代表固定字段已最终确认。实际响应必须定义枚举、可空字段、分页、错误结构和版本兼容策略。

### 7.2 澄清状态

```json
{
  "request_id": "req_457",
  "status": "clarification_required",
  "clarification": {
    "question": "你希望使用下单金额还是支付金额？",
    "options": [
      { "label": "下单金额", "value": "order_amount" },
      { "label": "支付金额", "value": "paid_amount" }
    ]
  }
}
```

### 7.3 通用错误结构

```json
{
  "request_id": "req_458",
  "status": "failed",
  "error": {
    "code": "QUERY_POLICY_REJECTED",
    "message": "该查询不符合当前安全策略。",
    "retryable": false
  }
}
```

建议状态枚举：processing、clarification_required、completed、no_data、partial、failed。建议错误码至少包括 AUTH_REQUIRED、FORBIDDEN、SEMANTIC_AMBIGUOUS、SQL_POLICY_REJECTED、QUERY_TIMEOUT、RESULT_LIMIT_EXCEEDED、DATA_SOURCE_UNAVAILABLE、MODEL_TIMEOUT、INTERNAL_ERROR。

## 8. LLM 调用与结构化输出

LLM 负责语义理解、澄清文案、查询计划辅助和结果摘要；确定性的代码负责授权、SQL 安全、查询执行和数值校验。

- 模型输出使用 JSON Schema / 结构化输出，包含 intent、metrics、dimensions、filters、time_range、ambiguities、next_action 等字段。
- 对输出进行 schema 校验、枚举校验、字段白名单校验和业务规则校验；失败后最多有限次重试。
- 不要让模型自行生成或修改用户权限；权限上下文由认证服务注入并由服务端强制执行。
- Prompt 中提供经过筛选的语义目录，不要把整个数据库结构和敏感样本全部塞入上下文。
- 模型摘要仅能引用查询结果中的事实；无法计算的结论应明确说明无法判断。
- 记录模型供应商、模型标识、Prompt 版本、语义版本、输入 / 输出 token、耗时和错误类型；日志按敏感数据政策脱敏。
建议为解析、SQL 规划、结果摘要分别设计 Prompt / Schema，避免一个超长 Prompt 同时承担所有职责。

## 9. SQL 安全与查询治理

- 数据源使用独立只读账号，限制可访问的 schema、表和列；尽可能使用只读副本。
- 使用 AST 解析器检查 SQL 类型，拒绝多语句、写操作、危险函数、系统目录访问及不允许的对象。
- 将用户授权转换为服务端可执行的策略；对行级和列级权限使用数据库策略或可靠的查询计划改写，并进行测试。
- 设置查询超时、最大返回行数、最大并发、最大时间范围以及必要的成本 / 扫描限制。
- 对 Join 关系、聚合和去重逻辑进行语义校验，防止一对多关联放大指标。
- 对于无法可靠解析或不支持的 SQL 方言，采取拒绝执行而不是放行。
- 执行前后记录 SQL 哈希、策略结果、耗时、行数与错误码；原始 SQL 是否保存及保存多久需单独定策略。
安全验收应由独立测试用例覆盖。不要将“模型被提示不能做危险操作”当成安全边界。

## 10. 结果校验与可视化协议

查询结果先转为内部统一的数据集对象，再由确定性代码生成表格和图表规格。图表类型应根据列类型、行数和用户意图选择。

```json
{
  "type": "line",
  "title": "按周销售额趋势",
  "x": { "field": "week", "type": "temporal" },
  "series": [{ "field": "sales_amount", "type": "quantitative", "unit": "currency" }],
  "sort": [{ "field": "week", "direction": "asc" }]
}
```

- 校验列名、类型、单位、时间顺序、空值、重复分组、异常大结果集和截断标志。
- 摘要数值由代码计算或从已验证聚合结果中读取，不依赖模型心算。
- 百分比变化需定义分母为零、负数、缺失基期的处理方式。
- 不允许执行模型生成的 HTML、JavaScript 或任意可执行图表代码。
## 11. 错误处理、重试与幂等

| 场景 | 处理策略 | 重试原则 |
| --- | --- | --- |
| 模型超时 | 返回可恢复错误或使用有限降级 | 仅对可重试错误有限次重试 |
| 模型结构无效 | 校验失败，记录脱敏响应摘要 | 可进行一次修复 / 重试，仍失败则停止 |
| 数据库超时 | 取消或终止查询，释放连接 | 仅对安全且幂等的只读查询考虑重试 |
| 客户端重复提交 | 使用 request_id / 幂等键防止重复运行 | 同一幂等键返回已有状态 |
| 权限服务异常 | 失败关闭，不允许绕过权限继续执行 | 不因重试失败而放宽授权 |
| 部分结果 | 明确标记截断或部分失败 | 不能伪装为完整结果 |
| 用户取消 | 尽可能取消正在执行的查询和模型任务 | 记录取消状态 |

每个请求应有统一的状态流转和终态定义。重试需要有上限、退避策略和总耗时预算，避免失败时放大模型成本或数据库负载。

## 12. 日志、监控与审计

- 请求级追踪：request_id、conversation_id、user_id、数据域、阶段、状态、耗时。
- 质量指标：标准问题集准确率、澄清率、SQL 校验拒绝率、执行成功率、结果反馈分布。
- 性能指标：端到端延迟、模型延迟、数据库执行时间、P50 / P95、超时率。
- 成本指标：每次请求的模型调用次数、token 用量、查询资源消耗和失败重试成本。
- 安全审计：权限拒绝、危险 SQL、敏感字段访问尝试、管理员配置变更。
- 日志不得默认包含完整凭据、访问令牌、未脱敏个人数据或不必要的原始结果集。
## 13. 测试策略

| 测试层级 | 测试内容 | 关键方法 |
| --- | --- | --- |
| 单元测试 | 语义映射、日期处理、权限策略、SQL 规则、状态转换 | 固定输入 / 输出，覆盖边界条件 |
| 契约测试 | API Schema、模型 JSON Schema、数据源适配器 | 校验字段、类型、枚举和兼容性 |
| 集成测试 | 数据库连接、只读账号、查询执行、取消与超时 | 测试数据库与受控测试数据 |
| 回归评测 | 标准业务问题集与预期 SQL / 结果 | 固定模型 / Prompt / 语义版本，记录差异 |
| 安全测试 | 注入、越权、危险 SQL、数据泄漏、资源耗尽 | 红队用例与负向测试 |
| 性能测试 | 并发、慢查询、长上下文、模型限流 | 测量 P50 / P95、错误率和成本 |
| 用户验收 | 真实业务问题、指标口径和可解释性 | 业务负责人按场景签收 |

每次修改 Prompt、模型、语义目录、SQL 生成逻辑或权限策略，都应运行对应回归集。模型版本升级应先在评测环境对比，不直接无验证上线。

## 14. 部署、配置与回滚

- 区分开发、测试、预发布和生产环境；凭据通过密钥管理或环境注入，不提交到代码仓库。
- 数据库迁移需版本化，并先在测试环境验证。
- 模型、Prompt、语义目录和安全策略应可独立版本化，记录每次发布的版本组合。
- 发布前执行冒烟测试、权限测试和标准问题回归；上线后观察错误率、延迟、成本和拒绝率。
- 出现正确率显著下降、安全问题或资源异常时，可回滚模型 / Prompt / 语义版本；数据库结构变更需有兼容与回滚方案。
- 配置项建议包括：模型与超时、最大重试次数、查询超时、最大行数、允许的数据对象、日志脱敏、会话保留期。
## 15. 开发前需要确认

- 实际数据源、SQL 方言、连接方式、数据量、数据新鲜度和是否有只读副本。
- 首期业务域与关键指标口径，谁负责维护和审批语义配置。
- 认证方式、租户隔离、角色权限、行列权限和敏感数据分类。
- 模型部署位置、供应商限制、预算、延迟要求及是否允许数据发送到外部模型。
- 预期用户数、并发、SLA、日志保留、灾备和部署环境。
- 是否要求导出、分享、定时报告、收藏查询、自然语言生成固定报表等扩展功能。
## 16. 关键设计决策记录（ADR）

| ID | 决策主题 | 建议决策 | 状态 |
| --- | --- | --- | --- |
| ADR-001 | 整体架构 | 模块化单体起步，保留模块接口 | 待评审 |
| ADR-002 | 查询权限 | 只读连接 + 服务端策略 + SQL AST 校验 | 必须确认 |
| ADR-003 | 业务口径 | 语义层作为指标与维度的唯一可信定义来源 | 建议采纳 |
| ADR-004 | 模型职责 | 模型负责理解和生成候选方案，确定性代码负责安全与执行 | 建议采纳 |
| ADR-005 | 评测 | 维护版本化标准问题集，变更后执行回归 | 建议采纳 |

ADR 记录最终决定、决策人、日期、替代方案和原因。设计发生变化时更新 ADR，而不是只修改正文。
