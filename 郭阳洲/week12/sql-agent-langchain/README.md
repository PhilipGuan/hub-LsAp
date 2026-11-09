# Chinook SQL Agent（ChatBI · LangChain + LangGraph 版）

用中文自然语言查询 `chinook.db`，完成「意图门禁 → 语义解析 → 选表 → SQL 生成 → 校验/改写 → 只读执行 → 空结果归因 → 受约束汇总 → 审计与反馈」的完整 ChatBI 工作流。

本项目由手写编排的原版（`06_SQL-Agent-Project`）改造为 **LangChain + LangGraph** 搭建：

- **LLM 层**：`langchain-openai` 的 `ChatOpenAI` 经 OpenAI 兼容接口接入 DeepSeek；`ChatPromptTemplate` 集中管理提示词；选表走 `with_structured_output`（Pydantic schema）结构化输出，失败自动降级 JSON 解析。
- **编排层**：`langgraph` 的 `StateGraph` 把原 10 步手写流程平移为 13 节点状态机，条件边实现拒答短路、校验/改写循环与预算熔断，改写环带 `recursion_limit` 双保险。
- **对外契约不变**：`SQLAgent.run()` 签名、`AgentResult` 40+ 字段、错误码体系（E1001–E8009）与原版完全一致，CLI / Streamlit UI / 评测集零改动复用。

## 工作流（LangGraph 状态机）

```text
用户问题（中文）
   |
   v
0. 输入检查        空输入 / 超长 / 危险意图（删改、预测、元问题、闲聊）
   |
   v
1. 语义解析        指标口径（revenue / sales_volume / ...）、时间范围、实体别名
   |
   v
2. Table 选择      规则提示 + LLM 选表 + 外键图补桥接表
   |
   v
3. SQL 生成        DDL + 样例行 + 值域 + 指标/时间约束 + few-shot
   |
   v
4. SQL 校验        sqlglot 静态校验（表白名单、列/CTE、GROUP BY、笛卡尔积、PII）
   |                + 函数白名单 + EXPLAIN 动态校验
   v
5. SQL 改写        校验/执行失败时带错误信息反思修复（最多 3 次）
   |
   v
6. SQL 执行        只读 URI、超时中断、最多返回 100 行、敏感列脱敏
   |
   v
7. 空结果归因      out_of_range / conditions_too_strict / true_zero / unknown
   |
   v
8. 受约束汇总      结构化 claims + 程序化 grounding，失败回退模板答案
   |
   v
9. 审计与反馈      trace_id + JSONL（30 天保留）、赞/踩反馈、试点用户码
```

## 目录结构

```text
sql-agent-langchain/
├── sql_agent/
│   ├── config.py        # 环境变量与配置
│   ├── errors.py        # 错误码与错误分类
│   ├── budget.py        # 截止时间 / LLM 调用数 / token / 成本预算
│   ├── llm.py           # LangChain ChatOpenAI 封装（预算记账 + 错误映射 + 结构化输出）
│   ├── catalog.py       # 数据库解析与缓存
│   ├── intent.py        # 意图门禁
│   ├── semantic.py      # 指标、时间范围、实体别名
│   ├── selector.py      # 选表（with_structured_output，失败降级 JSON）
│   ├── generator.py     # SQL 生成
│   ├── validator.py     # 静态/动态 SQL 校验
│   ├── rewriter.py      # SQL 改写
│   ├── executor.py      # 只读执行与脱敏
│   ├── contracts.py     # 结果契约、空结果归因、grounding、模板答案
│   ├── summarizer.py    # 受约束结果汇总
│   ├── privacy.py       # PII 列与日志脱敏
│   ├── trace_store.py   # JSONL 审计 + SQLite 反馈
│   ├── state.py         # LangGraph AgentState 定义（TypedDict + reducer）
│   ├── graph.py         # LangGraph 状态机（13 节点 / 条件边 / 改写环）
│   ├── agent.py         # SQLAgent 门面（run 签名与 AgentResult 与原版一致）
│   └── prompts.py       # ChatPromptTemplate 提示词集中管理
├── semantic/
│   ├── metrics.yaml     # 指标口径定义
│   └── entity_aliases.yaml
├── eval/
│   ├── gold.jsonl       # 130 题评测集（含 gold SQL 与期望状态）
│   ├── _gold_build.py   # gold 构建工具
│   ├── evaluate.py      # 评测 runner / gate / 基线回归
│   └── baseline.json    # 回归基线（保存后生成）
├── tests/               # 133 项离线测试（不调用真实 LLM）
├── docs/                # 改造方案文档
├── data/chinook.db      # 数据库（项目内自包含）
├── app.py               # Streamlit 聊天式 UI
├── main.py              # 命令行入口
├── sql_agent_langgraph_demo.ipynb # 分步演示 + 状态机可视化
├── .env.example
└── requirements.txt
```

## 快速开始

```bash
cd sql-agent-langchain
pip install -r requirements.txt

# 配置 DeepSeek Key
cp .env.example .env
# 编辑 .env，填入 DEEPSEEK_API_KEY

# 命令行
python main.py "销售额最高的前5个国家是哪些？"
python main.py -i
python main.py "有多少张发票？" --user-code pilot-01
python main.py "有多少张发票？" --json

# Streamlit UI
streamlit run app.py

# 离线测试（不调用 LLM）
pytest tests -q

# 评测
python eval/evaluate.py --verify-gold          # 只校验 gold 与安全/意图期望
python eval/evaluate.py --limit 10
python eval/evaluate.py --id h06,b01
python eval/evaluate.py --runs 3               # 全量 130 题 × 3 次
python eval/evaluate.py --runs 3 --save-baseline
```

`.env` 示例：

```bash
DEEPSEEK_API_KEY=sk-你的key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
CHINOOK_DB=data/chinook.db

# Agent 行为
SQL_AGENT_MAX_REWRITE=3
SQL_AGENT_MAX_ROWS=100
SQL_AGENT_QUERY_TIMEOUT=10
SQL_AGENT_DEADLINE=20
SQL_AGENT_MAX_LLM_CALLS=8
SQL_AGENT_MAX_TOKENS=60000
SQL_AGENT_MAX_COST=0.10

# 审计与反馈
SQL_AGENT_RETENTION_DAYS=30

# 评测
SQL_AGENT_EVAL_RUNS=3
```

## 关键设计

### LangChain / LangGraph 架构

- **LLM 层（`llm.py`）**：`ChatOpenAI` 按 (timeout, temperature, max_tokens) 缓存实例，`max_retries=0` 由应用层统一处理重试；usage 从 `AIMessage.usage_metadata` 提取（兼容 `response_metadata` 回退），逐次写入请求级 `Budget`（截止时间 / 调用数 / token / 成本）；openai 异常映射为原版错误码（429→E8002、401→E8001、超时→E4004 等）。
- **提示词（`prompts.py`）**：全部改为 `ChatPromptTemplate`，模板文本与原版逐字一致（字面花括号 `{{ }}` 转义），`tests/test_prompts_parity.py` 对照源项目逐字节校验渲染等价。
- **结构化输出**：选表用 `llm.chat_structured(messages, SelectionOut)`（内部 `with_structured_output(schema, include_raw=True)`），解析失败且预算仍够时降级 `chat_json` 兜底。
- **状态机（`state.py` + `graph.py`）**：`AgentState` 为 `TypedDict`（steps/warnings/assumptions/history 用 `operator.add` reducer 合并）；`build_graph(deps)` 闭包工厂每请求编译新图；条件边实现：意图/语义拒答短路、安全红线不重试直达 finalize、`attempts > max_rewrites` 或预算不足停止改写、`RewriteNoopError`（E6010）终止；`recursion_limit = 2*max_rewrites+20` 兜底防死循环。
- **可测试性**：`LLM(client=...)` 支持注入 `GenericFakeChatModel` / MagicMock，133 项离线测试不调用真实 LLM。

### ChatBI 能力（与原版一致）

- **意图门禁**：先于任何 LLM 调用拦截删改、预测/因果、元问题、闲聊、模糊问题和超范围表达，返回可读错误码与引导建议（E1001–E1007）。
- **语义层**：`semantic/metrics.yaml` 定义 revenue（账单粒度）、revenue_line（行粒度）、sales_volume、customer_count、track_count、avg_spend_per_customer 等口径；未定义指标（如客单价、利润）直接拒答 E7005，不猜公式。时间范围以 `data_until=2013-12-22` 为锚，支持相对时间、上下半年、年月与区间。
- **选表**：中文关键词提示 + LLM 输出 JSON 表列表；外键图补桥接表；选表失败回退全部表。
- **SQL 校验**：sqlglot 解析 AST，限制单条 `SELECT`/`WITH`、表白名单、列存在性、CTE 作用域、GROUP BY 完整性、禁止笛卡尔积、最多 8 表；函数白名单；禁止 `SELECT *` 与直接投影 PII 列；只读连接 `EXPLAIN` 做动态校验。
- **SQL 改写**：校验或执行失败时，把 schema、原 SQL、错误信息与最近尝试交给 LLM 修复，最多 `SQL_AGENT_MAX_REWRITE` 次。
- **SQL 执行**：`file:...?mode=ro` 只读连接、`set_progress_handler` 超时中断、`fetchmany(max_rows+1)` 截断、敏感列脱敏。
- **空结果归因**：区分时间越界、条件过严、真实为零和未知原因，给出不同的用户提示。
- **受约束汇总**：汇总器返回结构化 claims，程序逐条核对数值来源与合计；grounding 失败或汇总异常时回退模板答案。
- **预算**：单请求 20s 截止、最多 8 次 LLM 调用、token 与成本上限；超限时中止并给出明确错误码。
- **可观测性**：每一步生成 `TraceStep`；每次运行写入 `traces/` JSONL，UI/CLI 展示 trace_id、错误码、指标/时间口径、LLM 调用数、tokens 与成本。

## 评测与门槛

`eval/gold.jsonl` 共 130 题：

| 分片 | 题数 | 用途 |
|---|---|---|
| dev | 30 | 开发迭代 |
| holdout | 30 | 主验收，不得针对性调参 |
| boundary | 20 | 时间/空结果/边界 |
| entity | 20 | 中文实体 → 库内英文值 |
| intent | 20 | 意图门禁拒答 |
| security | 10 | 注入与危险 SQL |

比较语义：结果集无序比较（ordered=false）或逐行 + 并列组容错（ordered=true）；数值容差 `abs<=0.01` 或 `rel<=0.001`；指标按 family 匹配；列名允许额外 ID 列。

门槛（`evaluate.py` 自动计算）：

- holdout ≥ 29/30，dev ≥ 28/30
- 核心 tag 各 ≥ 4/5，空结果/安全类失败 0 容忍
- 选表 precision ≥ 85%，recall ≥ 95%
- grounding 100%，安全红线 0 命中
- 3 次运行波动 ≤ 2 题
- 与基线相比下降 ≥ 2 题 → BLOCK

## 安全说明

- API Key 只从 `.env` / 环境变量读取，不写入代码与日志。
- SQL 只允许单条 `SELECT` / `WITH`，拦截 `INSERT/UPDATE/DELETE/DROP/ALTER/ATTACH/PRAGMA` 等。
- 执行使用 SQLite 只读 URI，即使校验被绕过也无法写入。
- PII 列（Email/Phone/Address/Company/姓名/城市/州/邮编/Fax）禁止直接投影或用于排序，结果与日志均脱敏；`COUNT(敏感列)` 允许。
- 查询有超时、最大返回行数、最大 SQL 长度与最大 join 表数限制。
- 审计 JSONL 30 天保留，反馈库仅保存 trace_id、用户码、问题与评分。

## 已知限制

- 中文实体到库内英文值依赖指标/别名配置与值域提示；未收录的同义词仍可能选表或映射偏差。
- 时间解析覆盖常见表达，但复杂自然语言范围（如“春节前后”）不在 MVP 内。
- 汇总答案的数值由 grounding 校验，但结论性措辞仍由 LLM 生成；失败时回退模板。
- 评测集覆盖六个分片，但不等同于完整业务验收。
