---
title: "ETF 投研 Agent - 规格设计说明书"
domain: "美股ETF研究"
paradigm: "Agentic Research - 骨架复刻 + 模块化增强"
type: "Specification"
version: "v1.0.0"
status: "Draft - 等待用户审核"
author: "Philip - TRAE Brainstorming 流程生成"
created_at: "2026-09-13"
last_reviewed_at: "2026-09-13"
next_action: "用户审核 spec → 通过后进入 Implementation Plan 阶段（TRAE-plan-mode）"
related_project: "Week8-agent - 综合案例-02 复刻 + 金融增强"
project_dir: "/Users/philipclaw/Downloads/padow-ai/Week8-agent/Week08/Part2-VibeCoding实操/综合案例-02 VibeCoding-Philip/etf-research-agent"
env_ref: "/Users/philipclaw/Downloads/padow-ai/Week8-agent/Week08/.env"
requirements:
  output_shape: "方案B - 生产级（单ETF + 强制同类矩阵）"
  data_source: "插件式数据源（免费 Yahoo+ETFDB+Bocha 为 MVP 主力，可选 Polygon/Alpha 付费）"
  compliance: "B1 强免责三位置注入 + B2 字段级来源标签 + B3 运行前必须显式确认免责"
  runtime_mode: "CLI 优先 + FastAPI 可选（双模式）"
  github_layout: "内部新建 etf-research-agent/ 英文仓库根（Git 上传目录）"
  enhancements:
    - "配置驱动 Prompt 风格切换（analyst/institutional/explainer）"
    - "结构化数据日级缓存（ResultCache 24h TTL）"
  implementation_route: "路线2 - 骨架级复刻 + 7增强模块（推荐并确认）"
  estimated_mvp_time: "2~3 天"
  estimated_loc: "≈ 4,000 LOC（35% 继承综合案例-02 / 65% 新增）"
---

# ETF 投研 Agent · 规格设计说明书 (Specification)

> ⚠️ **合规免责声明（Spec 文档自身）**：本设计描述的是一个自动化研究工具原型，仅用于技术学习与信息整理。所有基于该工具生成的研究产物**不构成任何投资建议**，历史业绩不预示未来表现。用户使用前须阅读工具生成的完整免责条款。

---

## 1. 设计背景与目标

### 1.1 目标

创建一个面向**美股单只 ETF 深度研究**的 Agent 系统，基于 Week8 课程「综合案例-02 深度研究助手」的工程骨架 1:1 继承其 6 大工程优点（确定性编排、Prompt 与代码分离、草稿累积事实锚定、双收敛机制、增量写盘、路径自定位），并针对美股金融投研场景的 7 项**固有弱点**做**模块化增强**（不改骨架，仅新增 7 个独立包），最终产出：

- **可上传 GitHub 公开仓库**的干净工程结构（英文目录 + .gitignore + .env.example + LICENSE + README）
- 以**单 ETF 代码（如 `SOXL`）** 为入口，5~10 分钟内产出 10 节的生产级投研报告 + 5~8 只同类对比矩阵
- **4 种导出格式**（JSON / Markdown / 自包含 HTML / 4 张 CSV 表），带行内引用溯源 + 数据新鲜度警告 + 合规免责三位置强注入
- 可选扩展路径：未来可自然升级为方案 C（主题 ETF 组合研究器），已有模块 100% 复用

### 1.2 范围边界（本次 MVP 在 Scope 内 / 外）

| In Scope (MVP) | Out of Scope (MVP 不做，留待后续里程碑) |
|---|---|
| 单只 ETF（输入 1 个 ticker）+ 5~8 只同类自动识别 | 主题 ETF 组合筛选器（方案 C，需额外 composer.py + PortfolioOptimizer）|
| 4 种结构化数据源（Yahoo Finance / ETFDB / Alpha Vantage / Polygon）+ Bocha 新闻搜索 | 13F 持仓 / SEC EDGAR 原文解析 / 期权链 Greeks 深度（可以后加 Provider）|
| 分析师三档评级（Bullish / Neutral / Bearish）仅为「LLM 生成的主题分类标签」 | 回测引擎 / 交易信号生成 / 下单 / 自动调仓 |
| 相关性矩阵 / Beta / Alpha / Sharpe 基础风险指标计算 | 高频因子 / 分钟级数据 / Level 2 盘口 |
| 3 种 Prompt 语气 + 4 级缓存 + 7 种失败降级 + 4 级测试计划 | 多用户账户 / 权限 / SaaS 化部署（FastAPI 是单用户本地模式）|

---

## 2. 设计决策摘要（Brainstorming 全部确认的 Gate）

| Gate 编号 | 问题 | 决策（用户确认）|
|---|---|---|
| Q1 | 输出形态 & 范围边界 | **方案B - 生产级（单 ETF + 强制同类 5~8 只矩阵）** |
| Q2-数据源 | 数据通道策略 | **插件式数据源（A+B）+ MVP 主力免费源，付费预留接口** |
| Q2-合规 | 免责强度 | **B1 强免责三位置 + B2 字段级来源标签 + B3 运行前显式确认** 全开 |
| Q3-模式 | 运行入口 | **CLI 优先 + FastAPI 可选（双模式）** |
| Q3-目录 | GitHub 布局 | **内部新建 etf-research-agent/ 英文仓库根**（Git 只上传这一整个子文件夹）|
| Q3-增强 1 | Prompt 风格 | **加入：3 档（analyst / institutional / explainer）配置驱动** |
| Q3-增强 2 | 缓存 | **加入：日级结构化缓存（24h TTL + 空值 1h 防 429 重试）** |
| Q4 | 实现路线 | **路线 2 - 骨架级复刻 + 7 增强模块**（100% 继承工程优点，系统修复 7 项金融弱点）|
| §5.1 | 4 层架构 | ✅ 已确认（Entry / Orchestrator / Agent / Data / Output） |
| §5.2 | GitHub 目录结构 | ✅ 已确认（43 文件，≈ 4,000 LOC） |
| §5.3 | 11 核心模块接口 | ✅ 已确认（Provider / Schema+Peer / Citation+Freshness / Exporter+Compliance / Cache+Style / 骨架 4 件套）|
| §5.4 | 8 阶段流水线 | ✅ 已确认（Provider→Freshness→Plan→Bocha→Summary→Judge→Report→Export） |
| §5.5 | 7 种失败降级策略 | ✅ 已确认（「必保最少产物」原则）|
| §5.6 | 4 级测试 | ✅ 已确认（L1+L2 MVP 必过 / L3 推荐 / L4 可选手动冒烟）|

---

## 3. 综合案例-02 第一性原理优缺点分析（继承 vs 增强依据）

### 3.1 继承的 6 大工程优点（100% 保持不动）

1. **确定性编排 vs LLM 解耦**：控制流（if/for/收敛/补抓）全在 `etf_engine.py` 写死，LLM 只做「内容生产 + 语言判断」，不让 LLM 自己决定"够不够"。
2. **草稿累积 + 事实锚定**：正文 sections 由每轮 `CitedParagraph` 直接映射，ReportAgent 高阶只生成 summary/conclusion，绝不重写正文（**最大程度防幻觉**）。
3. **JudgeAgent + MAX_ROUNDS 双收敛**：软判断（ETFSchema.is_complete() 驱动 Judge 结构化输出）+ 硬上限（MAX_ROUNDS=3）保证不会死循环。
4. **Jinja2 Prompt 与代码彻底分离**：所有 Prompt 存在 `backend/templates/*.jinja2`，`style_snippets/` 可 include 切换语气，调 Prompt 不动 py。
5. **增量写盘 on_progress**：Phase 每完成一个 kw/一个 section 就写盘，中途断网/崩溃不丢已产出内容；用户可轮询实时看到进度。
6. **路径自定位 + envify 全 env 化**：`config.py` 基于 `Path(__file__)` 相对定位；envify-llm `LLM_PROVIDER + DEEPSEEK_* / QWEN_*` 规范；对齐 Week08/.env 级联加载。

### 3.2 增强的 7 个金融投研特有弱点（全部系统修复）

| 弱点（综合案例-02 现状） | 金融投研影响 | 新增模块（7 大增强） |
|---|---|---|
| 单通道 Bocha Web 搜索，无法拿结构化数值 | 费率/AUM/夏普/资金流等核心数字从 snippet 抽误差高 | **M1 DataProvider 抽象 + 4 实现（插件式，付费优先免费兜底）** |
| 研究维度靠 LLM 发散关键词，没有强制 Schema | 很容易遗漏「费率/AUM/回撤/资金流」等必看节 | **M2 ETFSchema（强制 10 节 + PeerResolver 5~8 只同类）** |
| 来源溯源只到 URL 列表，没有行内引用 | 数字错了找不到哪段原文错的 | **M3 CitationParser（行内 `[YF-1]` 引用 + HTML 悬浮弹窗）** |
| 无时间语义，跨期数据会混淆 | YTD 2025 vs YTD 2022 放一起比报告作废 | **M3+ FreshnessValidator（窗口内=7d/30d/3d，标 stale→重抓→警告）** |
| 同类 ETF 对比是散落在正文，无矩阵一等公民 | ETF 决策核心是"同类里挑"，散文字无法比较 | **M2+ PeerResolver + PeerComparisonMatrix（HTML 矩阵表格）** |
| 只有 JSON + HTML 两种产物，无法二次消费 | 想接飞书/Notion/Excel 要自己转 | **M4 ReportExporter 4 格式（JSON/MD/HTML/4×CSV）** |
| 没有合规免责机制，公开仓库示例输出有风险 | 美股投研工具只要对外就必须有四件套 | **M4+ Compliance（B1 三位置免责 + B2 source_tag + B3 入口拦截）** |

增强（M1~M5）全部**独立新包**，不修改骨架 4 件套的代码结构与函数签名，模块间只通过 Pydantic 模型通信 → 完美符合「最小侵入」偏好。

---

## 4. 架构总览（4 层分层 + 数据流方向）

> 详细视觉化：项目目录里已生成 `_tmp_design_5_1.html`（本地浏览器直接打开）。

```
 ┌─────────────────────────────────────────────────────────────────────┐
 │ ① 入口层 Entry                                                      │
 │  ┌──────────────┐  ┌──────────────┐  ┌────────────────────────────┐ │
 │  │ cli.py (NEW) │  │ backend/app  │  │ config.py (envify-llm + B3)│ │
 │  │--disclaimer  │  │POST /api/etf │  │Week08/.env override 级联   │ │
 │  └──────┬───────┘  └──────┬───────┘  └────────────┬───────────────┘ │
 └─────────┼─────────────────┼───────────────────────┼─────────────────┘
           │                 │                       │
 ┌─────────▼─────────────────▼───────────────────────▼─────────────────┐
 │ ② 编排层 Orchestrator                                                │
 │  ┌────────────────────────┐   ┌────────────┐  ┌───────────────────┐ │
 │  │ etf_engine.py (8 阶段) │   │ storage.py │  │ SchemaManager +   │ │
 │  │ 确定性 if/for 全写死   │   │增量写盘+锁 │  │ PeerResolver (NEW)│ │
 │  └──────────┬─────────────┘   └────────────┘  └────────┬──────────┘ │
 └────────────┼────────────────────────────────────────────┼────────────┘
              │                                            │
 ┌────────────▼────────────────────────────────────────────▼────────────┐
 │ ③ Agent 层                                                           │
 │  ┌──────────┐ ┌─────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐ │
 │  │ Planning │ │ Summary │ │  Judge   │ │  Report  │ │StyleResolver │ │
 │  │  维度×词  │ │带引用抽 │ │Schema打钩│ │ 高阶+评级│ │ 3 档语气(NEW)│ │
 │  └────┬─────┘ └────┬────┘ └──────────┘ └──────────┘ └──────────────┘ │
 └───────┼─────────────┼─────────────────────────────────────────────────┘
         │             │
 ┌───────▼─────────────▼─────────────────────────────────────────────────┐
 │ ④ 数据 / 工具层 Data                                                   │
 │  ┌──────────────────┐ ┌──────────────────┐ ┌────────────────────────┐ │
 │  │ ProviderRegistry  │ │  Bocha 搜索工具  │ │  Citation 引用解析     │ │
 │  │(Yahoo/ETFDB/AV/   │ │  (同综合案例-02) │ │  + Freshness 新鲜度校验│ │
 │  │ Polygon + Cache)  │ │                  │ │                        │ │
 │  └────────┬──────────┘ └──────────────────┘ └────────────────────────┘ │
 └───────────┼────────────────────────────────────────────────────────────┘
             │
 ┌───────────▼────────────────────────────────────────────────────────────┐
 │ ⑤ 输出层 Output                                                         │
 │  ┌──────────────────────────────────────────────────────────────────┐  │
 │  │ ReportExporter（B1 免责强制注入）+ CitationRenderer 渲染引用      │  │
 │  │ → 7 文件 落盘                                                     │  │
 │  │ {ticker}_{date}.json  / .md  / .html                              │  │
 │  │ + core_metrics.csv / peer_matrix.csv / holdings.csv / quotes.csv  │  │
 │  └──────────────────────────────────────────────────────────────────┘  │
 └────────────────────────────────────────────────────────────────────────┘
```

### 4.1 模块间通信原则（硬约束）

- **所有模块之间只通过 `backend/models.py` 中定义的 Pydantic 对象通信**（ETFProfile / RiskMetrics / Holdings / FundFlow / PeerComparisonMatrix / Citation / CitedParagraph / ReportContent / Disclaimer / AnalystRating / Confidence 等）。
- **不允许**模块 A 直接读取模块 B 的内部状态；**不允许**循环 import（Provider → Schema → Citation → Cache → Styles → Agent → Engine → Exporter → Compliance，单向依赖）。

---

## 5. GitHub 目录结构（etf-research-agent/ 英文根目录）

> 详细视觉化：`_tmp_design_5_1.html` 内有完整树状图 + 新增/复刻/忽略 三色图例。

```
etf-research-agent/                         ⭐ GitHub 上传这个整个文件夹
│
├── README.md                              NEW · 顶部免责+安装指南+samples链接
├── LICENSE                                NEW · MIT
├── requirements.txt                       COPY+ · 综合案例-02 + yfinance/pandas/numpy
├── .env.example                           NEW · DEEPSEEK_* / QWEN_* / BOCHA_* / AV_* / POLYGON_*
├── .gitignore                             NEW · .env / data/ / cache/ / __pycache__ / _tmp_*
├── start.sh                               COPY · uvicorn backend.app:app
├── main.py                                NEW · CLI 入口：`python main.py SOXL --peers 5 --style analyst --disclaimer-agree`
│
├── backend/
│   ├── config.py                          COPY · 100% 继承刚改完的 envify-llm（Week08/.env 级联 + Week08/.env override）
│   ├── models.py                          COPY+NEW · 原综合案例模型 + 新增 10+ 金融模型（Profile/Risk/Holdings/FundFlow/Peer/Cit/Report/Disclaimer）
│   ├── storage.py                         COPY · threading.Lock + 状态机 pending/running/completed/failed
│   │
│   ├── data_providers/                    NEW · M1 插件式数据源
│   │   ├── __init__.py                     ProviderRegistry（按优先级串联+degrade判断）
│   │   ├── base.py                         DataProvider 抽象（7 方法契约 + is_available + source_tag）
│   │   ├── yahoo_finance.py                yfinance 库实现（MVP 主力）
│   │   ├── etfdb.py                        HTTP 抓取 ETFDB.com
│   │   ├── alpha_vantage.py                Key 有则启用（高优先级）
│   │   └── polygon.py                      Key 有则启用（最高优先级）
│   │
│   ├── schemas/                           NEW · M2 Schema + Peer
│   │   ├── etf_schema.py                   DEFAULT_B_SECTIONS 10 节 + missing_required/is_complete
│   │   └── peer_resolver.py                规则优先 + LLM 兜底补对标；assemble_matrix 返回 PeerComparisonMatrix
│   │
│   ├── citations/                         NEW · M3 Citation + Freshness
│   │   ├── citation.py                     [A-Z]+-\d+ 正则解析 → CitedParagraph 绑定
│   │   └── time_window.py                  DEFAULT_POLICIES（YTD=7d, fundflow=3d, aum=30d）+ stale 列表输出
│   │
│   ├── exporter/                          NEW · M4 Exporter 4 格式 + Disclaimer
│   │   ├── base.py                         ReportContent + Disclaimer 注入（B1 3位置）
│   │   ├── json_exporter.py                全量 Pydantic
│   │   ├── md_exporter.py                  Markdown + 脚注
│   │   ├── html_exporter.py                自包含HTML + 矩阵CSS + 引用悬浮弹窗（JS）
│   │   └── csv_exporter.py                 4×CSV（每表强制 source_tag 列 B2）
│   │
│   ├── cache/                             NEW · M5 ResultCache 日级
│   │   └── result_cache.py                 {ticker}_{date}_{method}_{args}.json；TTL=24h，empty=1h
│   │
│   ├── compliance/                        NEW · B1/B2/B3 合规
│   │   ├── disclaimer.py                   Disclaimer Pydantic（四件套）+ 4 种渲染（CLI/MD/HTML/CSV注释）
│   │   └── source_tag.py                   PROVIDER_TO_TAG（YF/ETFDB/AV/POLY/BOCHA）
│   │
│   ├── styles/                            NEW · Prompt 风格
│   │   └── prompt_style.py                 Enum (ANALYST/INSTITUTIONAL/EXPLAINER) + from_cli_or_env + jinja_context
│   │
│   ├── agent/                             COPY+NEW · 4 Agent + BaseAgent（style 变量注入）
│   │   ├── base.py                         99% 综合案例-02（parse_json 兜底 + LLM_RETRIES 重试）
│   │   ├── planning.py                     KeywordAgent → PlanningAgent：{section_id: [kw1,kw2,kw3]}
│   │   ├── summary.py                      强约束 [PROV-N] 引用标签输出
│   │   ├── judge.py                        输出改为（completed/missing/new_kw_by_section）
│   │   └── report.py                       AnalystRating + markdown 矩阵表格 + 高阶 summary
│   │
│   ├── templates/                         COPY+NEW · jinja2 模板
│   │   ├── planning_agent.jinja2
│   │   ├── summary_agent.jinja2             改：必含引用；注入 {{style}}
│   │   ├── judge_agent.jinja2               改：按 Schema 打钩结构
│   │   ├── report_agent.jinja2              改：评级+矩阵+免责顶栏
│   │   ├── report_html.jinja2               改：矩阵CSS+引用悬浮+警告条+B1 banner
│   │   └── style_snippets/                  NEW · analyst.md / institutional.md / explainer.md
│   │
│   ├── tools.py                             COPY · Bocha 搜索（99% 综合案例-02）
│   ├── etf_engine.py                        COPY+NEW · 8 阶段流水线（原 engine.py 改名，金融化）
│   ├── etf_service.py                       COPY · 原 research.py（桥接 engine 和 storage）
│   └── app.py                               COPY · 原 app.py（路由改 /api/etf/{ticker}，B3 参数校验 400 拦截）
│
├── data/                                GITIGNORE · 研究记录 + 7 种导出文件（不 Git）
│   └── etf_records/                         每次运行的完整 storage JSON
│
├── cache/                               GITIGNORE · Provider 日级缓存
│
├── samples/                             NEW · GitHub 公开示例输出（脱敏，不含投资建议语气）
│   ├── SOXL.md
│   ├── SOXL.html
│   ├── SOXL_core_metrics.csv
│   └── SOXL_peer_matrix.csv
│
└── tests/                               NEW · 4 级测试
    ├── test_config.py                      L1 配置 + 合规 B3 拦截
    ├── test_disclaimer.py                  L1 B1 三位置注入
    ├── test_schema.py                      L2 Schema 缺节判断 + PeerResolver
    ├── test_citation.py                    L2 Citation 解析 + Freshness
    ├── test_exporter.py                    L2 4 格式导出 + B2 source_tag
    ├── test_providers.py                   L3 Yahoo+ETFDB+Registry+Cache（需网络，可跳过）
    └── test_engine_smoke.py                L4 端到端（需 LLM+网络，可选手动冒烟）
```

### 5.1 规模估算

| 分类 | 文件数 | 粗估 LOC | 代码复用率 |
|---|---|---|---|
| 骨架复刻（橙色 COPY） | 11 文件 | ≈ 1,400 LOC | 95%（综合案例-02 直接继承）|
| 7 大增强模块（绿色 NEW） | 20 文件 | ≈ 1,800 LOC | 0%（全新）|
| 工程配置 README/.gitignore/tests/samples/ | ≈ 12 文件 | ≈ 800 LOC | 0%（全新）|
| **合计** | **≈ 43 文件** | **≈ 4,000 LOC** | **35% 继承 / 65% 新增** |

---

## 6. 核心模块接口契约（11 个模块 · 对外公开签名）

> 详细视觉化 & 每个字段解释：`_tmp_design_5_3.html`（含完整字段级签名）。这里只列出模块名 + 公开方法 + 返回类型 + 合规标注。

### M1 - DataProvider 插件式 + Registry

- **文件**：`data_providers/base.py` + 4 实现 + `__init__.py` Registry
- **DataProvider 抽象（7 方法契约）**：`is_available() -> bool`；`get_profile(t)` → ETFProfile（含 as_of + source_tag）；`get_history(t, period)` → QuoteHistory；`get_risk_metrics(t, bench)` → RiskMetrics；`get_holdings(t, top_n=15)` → Holdings；`get_fund_flow(t)` → FundFlow；`get_peer_info(cat,idx,aum,tickers)` → PeerInfoBatch
- **Registry 行为**：providers 顺序 [polygon, av, yahoo, etfdb]（付费前免费后）；`fetch_first_available(method, ticker)` 逐个试，成功返回 (result, provider_name, degraded=False)；全失败 degraded=True → 标记 Bocha 兜底

### M2 - ETFSchema + PeerResolver

- **ETFSchema 行为**：10 DEFAULT_B_SECTIONS（封面摘要/基本信息/业绩/风险/持仓/资金流/同类矩阵/宏观/评级/遗留问题），每节 required=True + min_text_chars；`missing_required_sections(已完成集合, 各节字数dict)` → 缺节列表；`is_complete()` → 全局收敛判断（硬替代原综合案例 sufficient 自由判断）
- **PeerResolver 行为**：`resolve_peers(main_profile, 5~8)` 规则（同 category/focus_index + AUM 0.5x~2x → 近邻排序 → 不够 → 放宽 0.3x~3x → 还不够 → KeywordAgent 变体 Bocha 补）；`assemble_matrix(main, main_risk, peers)` → PeerComparisonMatrix（1+N 行 ×12列，每行含 source_tag B2）

### M3 - CitationParser + FreshnessValidator

- **CitationParser 行为**：正则 `\[([A-Z]+)-(\d+)\]` 提取标签；映射到「结构化 YF/ETFDB/AV/POLY 引用库」或「BOCHA 搜索库」；`CitedParagraph(section_id, plain_text, citations[])` 绑定
- **FreshnessValidator 行为**：DEFAULT_POLICIES 按节字段卡 as_of（performance.ytd=7d, risk.volatility_1y=30d, fundflow.5d=3d, basic_info.aum=30d）；`check_all(report_date, results)` → (全局新鲜, StaleField列表)；`stale_fields_to_warnings` → HTML 黄色警告条字符串列表

### M4 - ReportExporter（4 格式）+ Disclaimer（合规 B1+B2）

- **ReportContent Pydantic（最外层结构）**：含 `ticker / report_date / style / sections(CitedParagraph[]) / peer_matrix / analyst_rating / stale_warnings / confidence / process_log`；**并且内建 `disclaimer: Disclaimer(version="v1.0", generated_at=now())` 字段**
- **4 种 Exporter**：
  - `json_exporter`：全模型 → JSON
  - `md_exporter`：sections → 各节 MD + 引用脚注 + Disclaimer 首段
  - `html_exporter`：自包含 HTML（内嵌 CSS 矩阵样式 + 引用悬浮 JS 弹窗 + stale_warnings 黄条 + B1 红底顶栏）
  - `csv_exporter`：4 张 CSV = core_metrics（主 ETF 1 行）/ peer_matrix（1+N 行）/ holdings_top10（10 行）/ quotes_daily（历史日频）→ **每张表首行 `# ` Disclaimer 注释 + 每表强制一列 `source_tag` B2**
- **Disclaimer 合规模型**：四件套 statements 固定；render_cli_banner / render_md_header / render_html_banner（红底 CSS）/ render_csv_header_comment 四方法

### M5 - ResultCache + PromptStyle

- **ResultCache**：KEY = `{ticker}_{report_date_YYYYMMDD}_{method_name}_{args_hash}.json`；`get(t,m,a,date,bypass=False)` → (hit, res)；`set(t,m,a,date,res)`；空值缓存 TTL 1h，正常值 TTL 24h；`prune_older_than(days=7)` → CLI 子命令
- **PromptStyle**：Enum ANALYST(华尔街卖方)/INSTITUTIONAL(机构)/EXPLAINER(科普)；`from_cli_or_env(cli_val)` → CLI > env `PROMPT_STYLE` > 默认 analyst；`jinja_context(style)` → 返回 `{'style_snippet_md': 'style_snippets/analyst.md'}` → BaseAgent 自动 merge 给 jinja 模板（模板里 `{% include style_snippet_md %}` 实现语气注入）

### M6 - 骨架 4 件套（复刻综合案例-02 · 仅 rename + ETF 化）

- **BaseAgent**：100% 综合案例-02（parse_json + LLM_RETRIES 空输出 3 次重试 + jinja2 渲染）；新增：渲染前自动 merge `StyleResolver.jinja_context`
- **PlanningAgent**（原 KeywordAgent）：输入 = profile + peers + 缺节 + style；输出 = `{section_id: [kw1, kw2, kw3]}` 字典
- **SummaryAgent**：强约束输出必须是 200~400 汉字/英文词，**至少含 3 个引用标签 `[PROV/N-N]`**；Summary 后立即进入 CitationParser.parse()，标签未匹配或引用无效 → 该段标 degraded，触发 Judge 补抓
- **JudgeAgent**：输入 = 已完成 sections + 字数 + 缺节 + 上轮草稿；输出 = `{completed_sections: str[], missing_sections: SectionSpec[], new_keywords_by_section: dict[str, list[str]], sufficient: bool}`；sufficient=True 当且仅当 ETFSchema.is_complete()=True
- **ReportAgent**（两次 LLM 调用）：
  1. `AnalystRating(ticker, main_profile, risk, peer_matrix, style)` → {rating(bullish/neutral/bearish), 3 drivers, 3 risks, open_questions[]}（明确标记「仅为 LLM 主题分类标签，非投资建议」）
  2. MD/HTML 渲染（用 report_html.jinja2，内嵌矩阵表格模板 + stale_warnings 黄条 + B1 banner）
- **etf_engine.py**：8 阶段写死流水线（下节 §7 详述），on_progress 回调每步立即写盘
- **etf_service.py + storage.py**：100% 综合案例-02（`threading.Lock` 并发写安全 + 状态机 `pending/running/completed/failed`；异常时保留中间结果写入 failed record）
- **app.py**：FastAPI 路由改为 `POST /api/etf/{ticker}`；B3 拦截：Body 中必须含 `agree_disclaimer=True` 字段，缺失或 False → **立即 400** 并返回 Disclaimer 全文；202 返回研究 ID；`GET /api/etf/{id}` 轮询；`GET /api/etf` 列表
- **CLI main.py**：`python main.py <ticker> [--peers 5|6|7|8] [--style analyst|institutional|explainer] [--report-date YYYY-MM-DD] [--max-rounds 3] [--cache-prune 7] [--output-dir ./data] [--disclaimer-agree]`；B3 拦截：无 `--disclaimer-agree` → **立即 exit(2)** 并打印 Disclaimer CLI 红色横幅 + `--disclaimer-agree` 使用指南

---

## 7. ETF 编排流水线（8 阶段 · etf_engine.py）

> 详细视觉化 + 耗时/调用统计表格：`_tmp_design_5_4_5_6.html`

| 阶段 | 名称 | 核心动作 | LLM 次数 | 写盘点 | 失败兜底 |
|---|---|---|---|---|---|
| 0 | 解析 + 信息 | ① CLI/API 参数校验 B3 拦截 ② ProviderRegistry 筛选可用 Provider ③ PeerResolver.resolve_peers → 5~8 只对标 | 0~1 次（补对标列表兜底）| 1× resolve_peers | 5 对标不足 → LLM 兜底 → 仍不足 → peer_basis 标「识别不足」，矩阵缩小行数 |
| 1 | Provider 批量抓取 | Cache 绕开前查；主+标 7 方法 × 7；主 Profile/History/7 周期 × Risk/Holdings/FundFlow；对标 PeerInfoBatch | 0 | 1× provider_fetch_done | 4 Provider 全失败 → degraded=True + 后续 Phase 4 100% 靠 Bocha 搜索抽取数值 |
| 2 | Freshness 校验 + 缺节识别 | check_all；stale 字段 → bypass_cache=True 重抓一轮；仍 stale → 入 stale_warnings；ETFSchema 初始化缺节清单（所有 required 节默认缺）| 0 | 1× freshness | 重抓仍 stale → 警告条 + confidence 降级（影响 overall）|
| 3 | Planning（维度×关键词） | PlanningAgent → 每个缺节 3 个关键词 | 1 次 | 1× plan | LLM 失败 3 次 → DEFAULT_KEYWORDS_BY_SECTION 硬编码关键词矩阵 |
| 4~N | 补抓正文（Bocha + Summary + Citation） | 逐 section_id × 逐 kw：Bocha 搜索 → 构造 ref_map(PROV/BOCHA) → SummaryAgent 强制含引用 → CitationParser.parse → 校验通过 → 写 CitedParagraph 到 sections；字数 < min_text_chars → 该节不标完成 | ≈2×N 缺节×M kw×R 轮 | 每 kw 1×search+1×summarize | 单 Summary/Bocha 失败 → continue 下一 kw；该段标 degraded 留空，等下一轮 Judge 补抓 |
| 5 | Judge 收敛 | JudgeAgent 按 Schema 打钩 + 生成缺节新关键词；sufficient=True 跳 6；round<MAX_ROUNDS 重入 4；round≥MAX_ROUNDS 硬跳出标 confidence 低；is_complete=True（硬逻辑）→ sufficient=True（强约束 | R 轮 | 每轮 1× judge | MAX_ROUNDS=3 强制跳出，confidence.notes 写「达到硬上限未收敛」）
| 6 | 报告高阶生成 | ReportAgent 两次调用 → AnalystRating + Summary+open_questions + Peer 矩阵 Markdown；sections 直接 CitedParagraph 映射（事实锚定）| 2 次 | 1× report | Report 失败 → analyst_rating/matrix 置空；正文 sections 已 write 盘，导出器能正常导出（只是高阶部分空）
| 7 | 置信度计算 + 4 格式导出 + 合规 B1+B2 | Confidence = Provider 成功率(30%) + stale 字段数(20%) + citation 引用覆盖率(30%) + Schema 完成度(20%)（high/medium/low）；Exporter.export_all → 7 文件 + 7；B1 三位置注入（MD 首段/HTML 顶栏/CSV 注释列 B2 source_tag 强制） | 0 | 1× completed | 任意 Exporter 失败 → try/except 跳过单个，保证 JSON 必写成功

**合计（平均 3 轮 + 5 对标）**：≈ **10~25 次 LLM 调用**；总耗时 **2~5 分钟（网络+缓存命中）**

---

## 8. 错误 & 降级策略（7 种典型失败场景 · 必保最少产物原则）

详细表格参见 `_tmp_design_5_4_5_6.html §5.5`。核心原则：
> 任何单模块失败不导致整条流水线失败；storage 记录一定能成功落盘；用户一定能拿到「至少一份结构化数据 JSON + 部分报告产物」。全部失败会被写入 confidence.notes 和 stale_warnings，不会静默吞。

MVP 质量门禁：4 Provider + Bocha 中 **至少 1 项可用** → 能产出报告（Confidence 至少 low）。

---

## 9. 测试计划（4 级 · L1/L2 MVP 必过）

| 层级 | 测试 | 网络依赖 | MVP 必过 | 典型执行时间 |
|---|---|---|---|---|
| L1 | test_config.py + test_disclaimer.py | 无（本地） | ✅ | <5s |
| L2 | test_schema.py + test_citation.py + test_exporter.py | 无（本地） | ✅ | <10s |
| L3 | test_providers.py | 🌐 Yahoo+ETFDB（可 mock） | ⭐ 推荐 | 15~30s |
| L4 | test_engine_smoke.py（完整跑 SOXL 1 次） | 🌐 + LLM | 可选（手动冒烟 1 次） | 3~8 分钟 |

**MVP 通过判定**（以下全满足即算「MVP 交付完成」）：
1. L1、L2 全部 pytest 通过 100%（本地无网必过）；
2. 手动跑 `python main.py SOXL --peers 5 --style analyst --disclaimer-agree` 成功，storage 最终 status=completed，Confidence ≥ medium，7 文件存在且 > 0 Bytes；
3. samples/ 目录补齐 1 份该次运行的脱敏示例输出（去除可能的「买/卖」等投资建议语气，仅保留中性描述）；
4. README 写好「安装 + CLI 用法 + API 用法 + 免责声明顶栏 + samples 链接」。

---

## 10. 未来里程碑（非 MVP Scope，仅预留扩展路径 · 方案 C 自然升级）

按「最小侵入」原则，本次 MVP 代码结构确保后续里程碑零破坏性修改：

| 里程碑 | 内容 | 对 MVP 代码侵入 |
|---|---|---|
| M2 · 方案 C · 主题筛选器 | NEW `composer.py`（主题分解 → 候选池筛选（AUM/费率/流动性硬阈值）→ 多 ETF 并行跑方案 B → 组合优化器（scipy 等权/风险平价/最小方差 3 套权重）→ 相关性矩阵+聚类+近 3 年累计 vs 基准）| 0（仅 NEW 顶层脚本，Provider/Schema/Exporter 模块 100% 复用）|
| M3 · SEC EDGAR Provider | NEW `data_providers/edgar.py`（13F/485BPOS 原文/风险章节解析）→ ProviderRegistry 里注册即可 | 0（只加 1 文件）|
| M4 · 多账户权限 SaaS 化 | FastAPI 加 OAuth2 JWT + SQLite user/record 表 | 只改 app.py + NEW auth/ 包 |
| M5 · RAGAS 自动打分（Week7） | NEW `evaluator/` 每次报告跑完，对每个 CitedParagraph 跑 Faithfulness（数值真实性）+ Context Precision（引用完整性），分数入库 | 0（可挂 etf_service on_completed 钩子）|

---

## 11. Self-Review（Spec 自检 4 项扫描 · 写 spec 时已执行）

| 自检项 | 结果 | 处理 |
|---|---|---|
| 占位符扫描（TBD/TODO/？？？未填空） | ✅ 无 | 所有键名、默认值、字段名全部明确写出 |
| 内部一致性检查（§3 的弱点 vs §5.2 模块是否对应；§7 流水线阶段数是否在 M6 接口中一致；B1/B2/B3 合规模块的三位置注入是否在 §6/§7 出现） | ✅ 全部对应 | 7 弱点 ↔ M1~M4 7 模块 一一对应；流水线 8 阶段与 etf_engine.py 行为描述一致；B1 3 位置 = CLI banner / MD 首段 / HTML 红顶 / CSV 注释，B2 = CSV source_tag + 所有数值 Pydantic source_tag，B3 = CLI exit(2) / API 400 拦截 |
| Scope 检查（是否混入了 M2/M3 的 Out of Scope 内容？） | ✅ 无越界 | 所有组合研究 / EDGAR / RAGAS 等都只在 §10 未来里程碑里，没有进入本次 MVP 的目录/模块/流水线 |
| 歧义检查（同一字段名不同地方是否有矛盾？比如 MAX_ROUNDS 在 engine 默认 3 vs Q4 文档说 3；ReportContent 是否包含 Disclaimer？）| ✅ 无歧义 | MAX_ROUNDS 默认值 = 3（engine/Question 文档一致）；ReportContent 内建 disclaimer 字段 §6.4 明确声明，Exporter §7 明确说 B1 三位置注入 |

---

## 12. Next Step（下一步）

本 Spec 经你审核通过后，下一步将进入 **TRAE-plan-mode（实现计划模式）**：
1. 把 43 文件 / ≈4K LOC 拆成按序执行的 Task 列表（骨架先搭 → Provider → Schema → Citation → Cache & Style → 4 Agent + 模板 → Exporter + Compliance → CLI + API → tests → samples + README）
2. 每个 Task 带「产物文件路径 + 验收标准 + 执行顺序依赖」
3. 你确认 Task 计划后，逐个 Gate 式执行（每个 Gate 完成后验收 + 跑 L1/L2 测试再进下一批）

> **请你审查本 spec 文件（`etf-research-agent/docs/specs/2026-09-13-etf-research-agent-spec-v1.0.md`）。如有修改点直接告诉我需要改哪一节（§1~§12 的任何地方都可以改）。改完最终版确认通过后 → 进入 Implementation Plan。**
