# ChatBI Demo 技术设计文档

**版本**：v0.1  
**文档类型**：技术设计文档（Technical Design Document）  
**上游依据**：PRD v0.1、项目设计文档 v0.1、功能设计文档 v0.1  
**适用范围**：Demo 版本，不含权限、多租户、脱敏、指标治理、高并发、审计、移动端。

---

## 1. 技术目标与约束

### 1.1 技术目标
- 打通“自然语言 → 口径 → SQL → 结果 → 图表/总结”端到端链路。
- 口径确认前不执行 SQL；SQL 只读、白名单、LIMIT、超时。
- 支持三类多轮：上下文继承、指代省略、下钻/换维。
- SQL 失败自动纠错 ≤2 次，仍失败仅提示。
- 本地可运行，单次响应 ≤10 秒（不含 LLM 排队）。

### 1.2 技术约束
| 项     | 约束                                               |
| ------ | -------------------------------------------------- |
| 前端   | Streamlit，原生图表 `st.line_chart`/`st.bar_chart` |
| 后端   | Python FastAPI                                     |
| LLM    | Qwen，OpenAI 兼容协议，OpenAI Agents SDK 接入      |
| 数据库 | 本地 MySQL 8.x，只读账号                           |
| 会话   | 内存存储，重启丢失可接受                           |
| 图表   | 仅折线/柱状，不做 ECharts/饼图/手动切换            |
| MCP    | 可选 P1，不阻塞主链路                              |

---

## 2. 总体架构

```text
┌─────────────────────────────┐
│ Streamlit 前端 (app.py)      │
│ - 聊天区 / 口径卡片 / 结果区  │
│ - st.session_state 存 session│
└──────────────┬──────────────┘
               │ HTTP JSON
┌──────────────▼──────────────┐
│ FastAPI 后端                 │
│ ┌─────────────────────────┐ │
│ │ API 层 chat.py           │ │
│ │ parse/refine/confirm/reset│ │
│ ├─────────────────────────┤ │
│ │ 服务层                   │ │
│ │ session / parser / sql   │ │
│ │ guard / executor / chart │ │
│ │ summary / metric         │ │
│ ├─────────────────────────┤ │
│ │ Agent 层 (OpenAI Agents) │ │
│ │ parser / sql / summary   │ │
│ ├─────────────────────────┤ │
│ │ 数据访问层 mysql_client  │ │
│ └─────────────────────────┘ │
└──────┬───────────────┬──────┘
       │               │
┌──────▼──────┐  ┌─────▼─────────────┐
│ Qwen API    │  │ MySQL (只读账号)   │
│ OpenAI兼容  │  │ chatbi_demo 样例库 │
└─────────────┘  └───────────────────┘
```

### 2.1 分层职责
| 层         | 职责                         | 不做什么              |
| ---------- | ---------------------------- | --------------------- |
| 前端       | 交互、状态展示、调用后端     | 不生成 SQL、不直连 DB |
| API 层     | 参数校验、路由、响应封装     | 不写业务规则          |
| 服务层     | 业务编排、规则、安全、状态   | 不直接拼 LLM Prompt   |
| Agent 层   | LLM 调用、结构化输出、纠错   | 不做 SQL 安全校验     |
| 数据访问层 | 连接、执行、超时、结果规范化 | 不做业务判断          |

---

## 3. 技术栈与依赖

### 3.1 运行环境
| 项     | 版本/要求   |
| ------ | ----------- |
| Python | 3.10+       |
| MySQL  | 8.x         |
| 浏览器 | Chrome/Edge |

### 3.2 依赖清单
```text
streamlit
fastapi
uvicorn[standard]
pydantic>=2
openai-agents
openai
pymysql
python-dotenv
pyyaml
pytest
```

### 3.3 配置项
`.env.example`：
```env
QWEN_API_KEY=
QWEN_BASE_URL=
QWEN_MODEL=
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=chatbi_ro
MYSQL_PASSWORD=
MYSQL_DB=chatbi_demo
QUERY_TIMEOUT_SECONDS=10
MAX_ROWS=1000
MAX_SQL_RETRY=2
LLM_TIMEOUT_SECONDS=30
ENABLE_MCP=false
```

`backend/utils/config.py` 用 `pydantic-settings` 或 `os.getenv` 读取，启动时校验必填项，缺失则快速失败。

---

## 4. 数据模型设计

### 4.1 样例库表结构
```sql
-- 地区维表
CREATE TABLE dim_region (
  region_id INT PRIMARY KEY,
  region_name VARCHAR(32) NOT NULL,
  parent_region VARCHAR(32)
);

-- 商品维表
CREATE TABLE dim_product (
  product_id INT PRIMARY KEY,
  product_name VARCHAR(64) NOT NULL,
  category VARCHAR(32) NOT NULL,
  sub_category VARCHAR(32),
  list_price DECIMAL(10,2)
);

-- 客户维表
CREATE TABLE dim_customer (
  customer_id INT PRIMARY KEY,
  customer_name VARCHAR(64),
  region_id INT,
  register_date DATE,
  customer_level VARCHAR(16),
  FOREIGN KEY (region_id) REFERENCES dim_region(region_id)
);

-- 日期维表
CREATE TABLE dim_date (
  date_key DATE PRIMARY KEY,
  year INT,
  quarter INT,
  month INT,
  week INT,
  is_weekend TINYINT
);

-- 订单事实表
CREATE TABLE fact_order (
  order_id BIGINT PRIMARY KEY,
  customer_id INT,
  product_id INT,
  region_id INT,
  order_date DATE,
  quantity INT,
  unit_price DECIMAL(10,2),
  discount DECIMAL(5,2),
  order_amount DECIMAL(12,2),
  status VARCHAR(16),          -- paid/refunded/cancelled
  refund_amount DECIMAL(12,2) DEFAULT 0,
  FOREIGN KEY (customer_id) REFERENCES dim_customer(customer_id),
  FOREIGN KEY (product_id) REFERENCES dim_product(product_id),
  FOREIGN KEY (region_id) REFERENCES dim_region(region_id)
);
```

索引建议：`fact_order(order_date)`、`fact_order(region_id)`、`fact_order(status)`、`fact_order(product_id)`。

### 4.2 指标口径元数据 `data/meta/metrics.yaml`
```yaml
metrics:
  sales_amount:
    name: 销售额
    expr: "SUM(f.order_amount)"
    filters: "f.status = 'paid'"
    description: 支付订单金额合计，不含退款
  order_count:
    name: 订单量
    expr: "COUNT(DISTINCT f.order_id)"
    filters: "f.status = 'paid'"
  avg_order_value:
    name: 客单价
    expr: "SUM(f.order_amount) / COUNT(DISTINCT f.order_id)"
    filters: "f.status = 'paid'"
  quantity:
    name: 销量
    expr: "SUM(f.quantity)"
    filters: "f.status = 'paid'"
  refund_rate:
    name: 退款率
    expr: "SUM(CASE WHEN f.status='refunded' THEN 1 ELSE 0 END) / COUNT(*)"
  refund_amount:
    name: 退款额
    expr: "SUM(f.refund_amount)"
    filters: "f.status = 'refunded'"

dimensions:
  month: {name: 月份, expr: "DATE_FORMAT(f.order_date, '%Y-%m')"}
  quarter: {name: 季度, expr: "CONCAT(YEAR(f.order_date), '-Q', QUARTER(f.order_date))"}
  year: {name: 年份, expr: "YEAR(f.order_date)"}
  region: {name: 地区, expr: "r.region_name"}
  category: {name: 品类, expr: "p.category"}
  product: {name: 商品, expr: "p.product_name"}
  customer_level: {name: 客户等级, expr: "c.customer_level"}
```

该文件同时作为 LLM Prompt 的 schema 上下文和 SQL 生成白名单来源。

### 4.3 会话状态模型（内存）
```python
SessionState = {
  "session_id": str,
  "history": [{"role": "user"|"assistant", "content": str, "spec": dict|None}],
  "last_metric_spec": dict | None,
  "last_result_columns": list[str] | None,
  "last_result_summary": str | None,
  "created_at": datetime,
  "updated_at": datetime,
}
```

`SessionService` 用字典 + 线程锁；加 TTL（如 2 小时）懒清理。

---

## 5. 接口设计

统一响应：
```json
{ "code": 0, "message": "ok", "data": { ... } }
```
错误码：`0` 成功；`1001` 参数错误；`1002` 解析失败；`1003` 口径不完整；`1004` 安全拦截；`1005` 执行失败；`1006` 超时；`1007` LLM 不可用；`1008` 会话不存在。

### 5.1 `POST /chat/parse`
请求：
```json
{ "session_id": "s1", "question": "2024年华东销售额按月统计" }
```
响应 `data`：
```json
{
  "spec": {
    "restatement": "你想查2024年华东地区销售额，按月统计",
    "metrics": [{"key": "sales_amount", "name": "销售额"}],
    "dimensions": [{"key": "month", "name": "月份"}],
    "filters": [
      {"field": "region", "op": "=", "value": "华东"},
      {"field": "year", "op": "=", "value": 2024}
    ],
    "order_by": [],
    "limit": null,
    "ambiguities": [],
    "confidence": 0.9
  },
  "raw_question": "2024年华东销售额按月统计"
}
```

### 5.2 `POST /chat/refine`
请求：
```json
{ "session_id": "s1", "base_spec": {...}, "refine_text": "改成按品类，只看华南" }
```
或结构化：
```json
{ "session_id": "s1", "base_spec": {...}, "spec_patch": {"dimensions":[...], "filters":[...]} }
```
响应同 parse，返回新 `spec`。

### 5.3 `POST /chat/confirm`
请求：
```json
{ "session_id": "s1", "spec": {...} }
```
响应 `data`：
```json
{
  "sql": "SELECT ... LIMIT 1000",
  "columns": ["月份", "销售额"],
  "rows": [["2024-01", 12345.6]],
  "row_count": 12,
  "truncated": false,
  "chart": {
    "type": "line",
    "x": "月份",
    "y": ["销售额"],
    "reason": "时间维度+单指标"
  },
  "summary": "2024年华东地区销售额按月统计……",
  "corrected": false,
  "retry_count": 0,
  "elapsed_ms": 1234
}
```
`chart.type` 枚举：`line` / `bar` / `none`。

### 5.4 `POST /chat/reset`
请求 `{ "session_id": "s1" }`，响应新 `session_id`。

### 5.5 `GET /health`
返回 `{ "status": "ok" }`。

---

## 6. Agent 设计

采用 OpenAI Agents SDK，Qwen 通过 OpenAI 兼容 `base_url` 接入。三个 Agent 职责分离，均要求 JSON 输出并用 Pydantic 校验。

### 6.1 Parser Agent（口径解析）
- 输入：用户问题、上一轮 `last_metric_spec`、`metrics.yaml` 摘要、维度/指标白名单、对话历史摘要。
- 输出：`MetricSpec` JSON。
- 规则：
  - 指标/维度只能取白名单 key；无法识别放入 `ambiguities`。
  - 多轮时基于上一轮 spec 合并。
  - 歧义必须显式列出，如指标不明确、时间范围不明确、指代不明。
- 失败处理：JSON 校验失败重试 1 次；仍失败返回 `1002`。

### 6.2 SQL Agent（生成与纠错）
- 生成输入：确认后的 `MetricSpec`、schema 摘要、指标/维度表达式、SQL 规范。
- 纠错输入：上次 SQL、脱敏错误、schema、spec。
- 输出：单条 SELECT SQL（不带分号、不带 markdown）。
- 规则：
  - 只允许 `SELECT`；只允许白名单表；必须带 `LIMIT`（由 guard 兜底）。
  - 不得生成多语句、DDL/DML、系统表查询。
  - 纠错最多 2 次，仅修 SQL 不改 spec。
- 失败处理：返回非 SELECT 或空 SQL 视为失败。

### 6.3 Summary Agent（文字总结）
- 输入：spec、SQL、列名、前 N 行结果（如 20 行）、总行数、是否截断。
- 输出：2～5 句总结文本。
- 规则：不编造结果外数据；空结果说明未查到；失败回退模板。

### 6.4 Prompt 与输出约束
- 所有 Agent 使用 system prompt 固定角色与输出格式，user 消息携带上下文。
- 输出用 JSON schema 描述，Pydantic 校验；校验失败带错误重试一次。
- 温度建议 0～0.2，保证稳定。

---

## 7. SQL 安全设计

### 7.1 安全策略
| 策略       | 实现                                                         |
| ---------- | ------------------------------------------------------------ |
| 只读账号   | MySQL 用户仅 `SELECT` 权限                                   |
| 仅 SELECT  | 正则/解析器校验首关键字，拦截 `;` 多语句                     |
| 表白名单   | 仅允许 `dim_region/dim_product/dim_customer/dim_date/fact_order` |
| 危险关键字 | 拦截 `INSERT/UPDATE/DELETE/DROP/ALTER/TRUNCATE/CREATE/GRANT/LOAD_FILE/INTO OUTFILE` |
| 系统表     | 拦截 `information_schema/mysql/performance_schema`           |
| LIMIT      | 无 LIMIT 自动追加 `LIMIT 1000`；有 LIMIT 超过 1000 改为 1000 |
| 超时       | 执行设置 `MAX_EXECUTION_TIME=10000` 或连接级超时             |
| 结果上限   | 最多返回 1000 行，超出标记 `truncated`                       |
| 错误脱敏   | 不返回连接串、密钥、系统表信息                               |

### 7.2 校验顺序
```text
SQL 非空 → 去 markdown/分号 → 单语句检查 → 首关键字 SELECT
→ 表白名单 → 危险关键字/系统表 → LIMIT 规范化 → 执行
```

### 7.3 执行器
- 使用 `pymysql`，连接池可简化为每次连接（Demo）。
- 设置 `read_timeout`/`connect_timeout`；执行前后记录耗时。
- 结果用 `cursor.description` 取列名，行转 list，`Decimal/date` 转字符串。
- 异常统一映射为错误码，错误文本脱敏后供纠错使用。

---

## 8. 多轮上下文设计

### 8.1 合并策略
- Parser Agent 输入包含 `last_metric_spec`，要求输出合并后的完整 spec。
- 合并规则：
  - 新问题明确指标 → 覆盖指标。
  - 只说“按月拆开” → 保留指标/过滤，新增维度。
  - “那华南呢” → 替换地区过滤。
  - “下钻到商品” → 新增商品维度或替换维度。
- 歧义：合并冲突（如指标矛盾、指代不明）写入 `ambiguities`，禁止确认。

### 8.2 指代识别
- 依赖上一轮 `last_result_columns` 与 `last_metric_spec`。
- 无上下文时提示“请先提出一个查询”。
- 指代不确定时要求用户明确，不猜测执行。

### 8.3 状态更新时机
- 仅在 `/chat/confirm` 成功后更新 `last_metric_spec` 与 `last_result_*`。
- 编辑未确认不更新。

---

## 9. 图表与总结

### 9.1 图表选型规则（`chart_service`）
```text
if 空结果: type=none
elif 时间维度 且 单指标: type=line, x=时间列, y=指标列
elif 分类维度 且 单指标: type=bar, x=分类列, y=指标列
elif 多指标 且 有维度: 取第一个指标，type=line/bar（按维度类型）
else: type=none（表格优先）
```
前端按 `type` 调 `st.line_chart` / `st.bar_chart`，数据用 `pandas.DataFrame` 构造。

### 9.2 总结生成
- 优先 Summary Agent；失败回退模板：
  `"按{维度}统计{指标}，筛选{filters}，共{row_count}行。"`
- 空结果模板：`"未查询到符合条件的数据，可尝试调整筛选条件或时间范围。"`

---

## 10. 前端设计（Streamlit）

### 10.1 状态
```python
st.session_state.session_id
st.session_state.messages      # 展示用
st.session_state.current_spec  # 待确认口径
st.session_state.last_result
```

### 10.2 交互
- 输入框 `st.chat_input`；消息用 `st.chat_message`。
- 口径卡片用 `st.form` + `selectbox/multiselect/text_input/number_input` 渲染可编辑字段。
- “重新解析”提交 `refine_text` 或结构化 patch；“确认执行”提交 `confirm`。
- 结果区：`st.line_chart`/`st.bar_chart` → `st.code(sql)`（`st.expander`）→ `st.dataframe` → `st.markdown(summary)`。
- 重置按钮清空 `st.session_state` 并调 `/chat/reset`。
- 请求用 `requests`，展示 spinner；错误用 `st.error`。

### 10.3 错误处理
- 后端错误码映射为友好提示。
- 纠错失败提示“可修改口径后重试”，不自动改卡片。

---

## 11. 异常、重试与超时

| 场景              | 策略                                   |
| ----------------- | -------------------------------------- |
| LLM 超时          | 30 秒超时，返回 1007，提示重试         |
| LLM 输出非法 JSON | 重试 1 次，仍失败返回 1002             |
| SQL 执行失败      | 纠错 ≤2 次，仍失败返回 1005 + 错误类别 |
| SQL 安全拦截      | 不纠错，直接 1004                      |
| 查询超时          | 返回 1006，不自动重试                  |
| 会话不存在        | 自动重建或返回 1008，前端提示重置      |
| 服务异常          | 统一 500 包装，脱敏                    |

**幂等**：`confirm` 非幂等，前端执行中禁用按钮防重复。

---

## 12. 日志与可观测（Demo 级）

- 结构化日志：`session_id`、接口、耗时、错误码、是否纠错、重试次数。
- 不记录 API Key、完整连接串、敏感数据。
- SQL 日志可记录但脱敏参数（Demo 可接受记录完整 SQL 便于调试，需在 README 说明）。
- 前端展示耗时（可选）。

---

## 13. 测试策略

| 层级       | 内容                                                     |
| ---------- | -------------------------------------------------------- |
| 单元测试   | `sql_guard` 拦截规则、LIMIT 规范化、指标表达式、图表选型 |
| Agent 测试 | 10 题解析快照、JSON 校验、纠错用例                       |
| 接口测试   | parse/refine/confirm/reset 正常与异常                    |
| 端到端     | 20 题验收、3 组多轮、纠错路径                            |
| 安全测试   | 非 SELECT、多语句、系统表、超 LIMIT                      |

**验收记录**：`tests/acceptance_cases.md` 记录每题口径、SQL、结果、耗时、结论。

---

## 14. 部署与运行

### 14.1 本地启动
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # 填 Qwen 与 MySQL
python data/seed.py
uvicorn backend.main:app --port 8000
streamlit run frontend/app.py
```

### 14.2 MySQL 只读账号
```sql
CREATE USER 'chatbi_ro'@'localhost' IDENTIFIED BY '***';
GRANT SELECT ON chatbi_demo.* TO 'chatbi_ro'@'localhost';
```

### 14.3 运行顺序
MySQL → seed → FastAPI → Streamlit。README 写清前置条件与常见问题（Qwen 兼容端点、MySQL 连接、端口占用）。

---

## 15. MCP 可选设计（P1）

- `mcp_server/server.py` 暴露工具：`list_tables`、`describe_table`、`run_readonly_query`。
- 复用 `sql_guard` 与 `sql_executor`，保证行为一致。
- 配置 `ENABLE_MCP=true` 时 Agent 优先走 MCP 工具；默认关闭。
- 不进入 P0 验收，失败不影响主链路。

---

## 16. 技术风险与应对

| 风险                              | 应对                                                   |
| --------------------------------- | ------------------------------------------------------ |
| Qwen 对 Agents SDK 工具调用兼容性 | 先做最小验证；降级为直接 Chat Completions + JSON 输出  |
| 结构化输出不稳定                  | Pydantic 校验 + 重试 + 枚举约束 + 温度 0               |
| 多轮合并偏差                      | 展示完整口径 + 用户可编辑 + 歧义阻止确认               |
| SQL 注入/越权                     | 只读账号 + 白名单 + 关键字拦截 + LIMIT + 超时          |
| Streamlit 图表受限                | 仅折线/柱状；不适合时不出图                            |
| 内存会话丢失                      | Demo 可接受；README 说明重启需重置                     |
| 性能                              | 限制结果行数、LLM 超时、查询超时；必要时并行总结与图表 |

---

## 17. 默认项与待确认

### 17.1 默认项
- 会话内存 + TTL 2 小时。
- LLM 温度 0～0.2，超时 30 秒。
- SQL 纠错 2 次，安全拦截不纠错。
- 图表仅 line/bar/none。
- 总结失败回退模板。
- MCP 默认关闭。

### 17.2 待确认
- Qwen 具体模型名与 base_url（需你提供）。
- 是否允许日志记录完整 SQL（Demo 调试便利 vs 脱敏）。
- 是否需要 `/chat/refine` 同时支持结构化 patch 与自然语言（建议都支持）。

