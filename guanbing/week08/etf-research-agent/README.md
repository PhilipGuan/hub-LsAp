# etf-research-agent · 美股 ETF 深度投研 Agent

> 🔴 **免责声明（B1 合规 · 顶栏必现）**：本项目由 AI 自动生成的研究工具，输出的一切内容不构成任何投资建议或买卖邀约。历史业绩不代表未来表现，投资有风险入市需谨慎。数据来源于第三方公开渠道，不保证其及时性与准确性。所有决策请咨询持牌金融顾问，作者不对任何损失承担责任。

**工程状态：MVP v1.0（首次发布 · 强调：这是 Version 1，后续版本将在 ① 分析框架深度 · ② Confidence 置信度算法 · ③ 响应速度（并行 Provider + Agent 流式） 三方面持续优化）**（42/42 L1+L2 pytest 100% PASS · Engine 8 阶段流水线写实 · 7 文件导出+B1+B2+B3 合规硬约束 100% 实现 · 预留 L3+L4 自测入口）

| 维度 | 详情 |
|---|---|
| 复刻基底 | Week8-agent 综合案例-02（确定性编排 + LLM 解耦 + Jinja2 Prompt + on_progress 增量写盘） |
| 研究方向 | 美股 ETF（单 ETF 深度研究 + **强制 5~8 只同类矩阵对比** · 方案 B） |
| 数据源 | 插件化 · 免费主力 Yahoo Finance + ETFDB · 可选付费 Polygon / Alpha Vantage · 所有结构化字段带 `source_tag` + `as_of`（B2 合规） |
| 合规 | B1 三位置免责声明（top/before_conclusion/footer） · B2 字段级溯源 · B3 CLI `--disclaimer-agree` + API `disclaimer_agree` 入口拦截（400 / exit(2)） |
| 运行模式 | **CLI 优先 + FastAPI 可选**（双模式，共用 `ETFService` Facade） |
| 目录根 | **etf-research-agent/** 英文根（Git 上传整个文件夹即可） |
| 增强 | ① Prompt 风格切换（analyst 券商研报风 / institutional 机构投顾 / explainer 科普风）· ② 结果缓存层（日级 TTL 24h · 空值 1h 防 429） |
| 实现路线 | 路线 2：骨架级复刻 + 7 大增强模块（见 Spec §10） |
| MVP 规模 | 43 文件 · ≈4,300 LOC |
| MVP 通过门禁 | ① L1+L2 pytest 42/42 · ② Phase7 真实写 7 文件（JSON/MD/HTML + 4×CSV）· ③ B1+B2+B3 100% · ④ samples/4 份脱敏样例齐全 |

---

## 🚀 安装 & 环境准备（4 步）

```bash
# 1. 进入英文仓库根目录（GitHub 上传的就是这个文件夹）
cd "综合案例-02 VibeCoding-Philip/etf-research-agent"

# 2. 环境变量（envify-llm 级联自动加载，优先写你自己的 Week08/.env，永远不要把真 Key 写进仓库）
#    优先级：./.env → ../.env → ../../Week08/.env（高优覆盖低优）
#
#    仅在你没有全局 Week08/.env 时，手动在本目录：
cp .env.example .env
#    然后编辑 .env，把 YOUR_* 替换成真实值（至少填 LLM_PROVIDER + *API_KEY + *BASE_URL + *MODEL 四件）

# 3. 依赖
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 4. envify 自检（正确识别 Provider + Key 安全校验通过 → 打印 [envify ✅]）
python3 -m backend.config
```

---

## 🛰 CLI 用法（MVP 真跑 · 主入口）

**核心子命令 3 个：`research` / `list` / `get`**

```bash
# ① 单 ETF 深度研究 + 强制 5~8 只同类矩阵对比
#    必须显式 --disclaimer-agree（B3 合规），否则 exit(2)
python main.py research SOXL \
    --peers 5 \
    --style analyst \
    --extra-peers SOXX SMH XLK VGT \
    --disclaimer-agree
#
# 真跑成功会打印 7 个导出文件大小的 ✅/❌0B 看板：
#   ✅ json: 60342B    ✅ md: 18900B    ✅ html: 21030B
#   ✅ profile_csv: 880B  ✅ history_csv: 18400B
#   ✅ holdings_csv: 1020B  ✅ peer_csv: 960B

# ② 列出最近 50 条研究记录
python main.py list --limit 50
#    · 每条 ID 形如 R_YYYYMMDD-HHMMSS_TICKER_xxxx（字母 ticker 前缀）

# ③ 查询单条记录详情（含 phase7 注册的 7 导出路径）
python main.py get R_20260913-181707_SOXL_44a32e --json
```

**style 可选值（Prompt 风格切换）：`analyst` 券商研报风 / `institutional` 机构投顾风 / `explainer` 科普风**

---

## 🔗 API 用法（FastAPI 模式 · 真 ETFService）

| Method | Path | 说明 | 请求体/参数 |
|---|---|---|---|
| GET  | `/health` | 健康检查（Provider / Model / B3 gate 状态） | — |
| POST | `/api/etf/{ticker}` | **启动投研（必须带 `disclaimer_agree: true`，B3 拦截 HTTP400）** | `{"disclaimer_agree":true, "style":"analyst", "peers":5, "extra_peers":["SOXX","SMH"]}` |
| GET  | `/api/etf/{research_id}` | 轮询研究进度/详情（ID 以 R_ 开头） | — |
| GET  | `/api/etf?limit=50` | 列出所有研究记录（字母 ticker 走 POST 不会冲突） | query: limit |

```bash
# 启动
bash start.sh api            # 等价：uvicorn backend.app:app --reload --port 8000

# ① health
curl -s http://127.0.0.1:8000/health | python3 -m json.tool

# ② B3 拦截（不带 disclaimer_agree → HTTP 400 + body.error + body.http_code=400）
curl -s -o /tmp/api_b3.err -w "%{http_code}\n" \
  -X POST -H "Content-Type: application/json" \
  -d '{"style":"analyst","peers":5}' \
  http://127.0.0.1:8000/api/etf/SOXL
#   输出：400

# ③ B3 放行（带 disclaimer_agree:true → HTTP 200 + body.status="pending" + body.research_id=R_xxx）
curl -s -o /tmp/api_post.ok -w "%{http_code}\n" \
  -X POST -H "Content-Type: application/json" \
  -d '{"disclaimer_agree":true,"style":"analyst","peers":5,"extra_peers":["SOXX","SMH"]}' \
  http://127.0.0.1:8000/api/etf/SOXL
#   输出：200 / 201（< 400 即放行）

# ④ 轮询（R_ 开头 id 直接 GET；另开终端跑因为 start.sh --reload 是 blocking）
curl -s http://127.0.0.1:8000/api/etf/R_$(cat /tmp/api_post.ok | python3 -c "import json,sys;d=json.load(sys.stdin);print(d['research_id'].split('_',1)[1])") 2>/dev/null | python3 -m json.tool
```

---

## 🧪 4 级测试门禁（与 Spec §5.6 对齐）

| Level | 内容 | 运行命令 | 跑通标准 | 当前状态 |
|---|---|---|---|---|
| **L0** | 语法检查 | `python3 -m compileall -q backend main.py tests` | 0 失败 | ✅ 0 / 0 |
| **L1** | Config envify + B3 合规（无网 · 全 mock） | `python3 -m pytest tests/test_l1_*.py -q` | 7/7 PASS | ✅ 7 / 7 |
| **L2** | Schema / Citation / Exporter / Agent Engine / Cache / Storage（无网 · 全 monkeypatch） | `python3 -m pytest tests/test_l2_*.py -q` | 35/35 PASS | ✅ 35 / 35 |
| **L3** | 真实 Provider（慢 · 有网才跑） | `python3 -m pytest tests/test_l3_providers.py -v --runslow` | Yahoo 字段 + ETFDB 字段 >0 或 degrade skip | 新文件已就位，自测按需 |
| **L4** | SOXL 端到端冒烟（真 LLM + 真 Yahoo/ETFDB 数据） | 见下方 `L4 SOXL 冒烟 · 4 门禁` | Spec §9 MVP 4 条全部满足 | 你自己本地手跑，需要真 Key + 外网 |

**L4 SOXL 冒烟 · 4 门禁（Spec §9，满足才算 MVP 真交付）：**

```bash
# 前置：Week08/.env（或本目录 .env）里至少：LLM_PROVIDER + *API_KEY 非空 + 外网能连 finance.yahoo.com
cd "综合案例-02 VibeCoding-Philip/etf-research-agent"

# 一键冒烟
python3 main.py research SOXL \
    --peers 5 \
    --style analyst \
    --extra-peers SOXX SMH XLK VGT QTEC \
    --disclaimer-agree
```

4 条门禁同时满足才算 🎉：
1. 退出码 0，console 打印 status=`completed`；
2. `Confidence≥medium`（最后打印的 JSON 块里 `report.confidence.overall` 为 `medium` 或 `high`）；
3. **7 个导出文件全部 >0B**：`report.(json|md|html)` + `profile/history/holdings/peer_matrix .csv`；
4. B1/B2/B3 抽验：md/HTML 顶部 + 结论前 + 页脚三处免责声明存在；4×CSV 列头最后两列必为 `as_of,source_tag`；不带 `--disclaimer-agree` 时 `echo $?` 等于 `2`。

---

## 📦 samples/ 脱敏样本文档（4 份 · 100% 假数据 · 方便 GitHub 展示）

全部已写在 `samples/` 下，**不含任何真实 Key / 真实 AUM / 真实收益率**，仅演示导出格式：

| 文件 | 用途 | 校验点 |
|---|---|---|
| `samples/report_sample.json` | ReportContent 完整 JSON（含 AnalystRating / PeerMatrix / Confidence / ProcessLog） | 10 节齐全 · citations 有 provider tag |
| `samples/report_sample.md` | 最终 Markdown 报告 | **B1 三位置免责声明：top ✅ · before_conclusion ✅ · footer ✅** |
| `samples/report_sample.html` | Markdown 包裹到 `<pre>` 的 HTML 版（浏览器直接双击打开） | 顶部有 meta charset=utf-8 · 中文不乱码 |
| `samples/peer_matrix_sample.csv` | 5 只同类 ETF 对比 CSV（SOXL/SOXX/SMH/XLK/VGT 假数据） | **B2 强制最后两列：`as_of,source_tag`** 列头存在 · 每行都有值 |

---

---

## 🗂 目录结构（与 Spec §5 对齐 · 三色标注）

| 类别 | 数量 | 状态 |
|---|---|---|
| 🟢 NEW · 纯新增（ETF 特有） | ≈28 | Dry-Run 骨架已就位 |
| 🟡 COPY · 继承综合案例-02 | ≈8 | Dry-Run 骨架已就位，实现时继承原逻辑 |
| ⚫ GITIGNORE（不上传） | ≈7 | .env / data/* / cache/* / _tmp_* 已忽略 |

```
etf-research-agent/                 # 🟢 GitHub 英文根（Git 上传整个文件夹）
├── main.py                         # 🟢 CLI 入口（research/list/get 子命令 · B3 合规拦截）
├── start.sh                        # 🟢 一键启动（自动 venv · research/api 双模式）
├── requirements.txt                # 🟡 依赖（继承基础 + yfinance/pandas 等金融依赖）
├── .gitignore                      # 🟢 .env + data/cache + _tmp_* 全忽略
├── .env.example                    # 🟢 envify-llm 三键位占位（无真实 Key）
├── README.md                       # 🟢 本文件（顶栏 B1 免责 + 安装 / 用法 / samples）
│
├── backend/                        # 🟡 代码根（继承综合案例-02 手感，业务重写）
│   ├── __init__.py
│   ├── config.py                   # 🟡 envify-llm 级联加载（Week08/.env override · 对齐 Philip 偏好）
│   ├── models.py                   # 🟢 Pydantic 类型中心（所有模块通信只传这里的对象）
│   ├── storage.py                  # 🟡 on_progress 增量写盘 · index.jsonl + 详情 JSON/HTML
│   ├── app.py                      # 🟡 FastAPI（health + POST/GET/list ETF · B3 合规）
│   │
│   ├── data_providers/             # 🟢 M1 · 插件式 Provider（Dry-Run 骨架）
│   │   ├── __init__.py
│   │   ├── base.py                 # 🟢 DataProvider 抽象（7 方法契约）
│   │   ├── registry.py             # 🟢 ProviderRegistry · fetch_first_available + degraded 判定
│   │   └── implementations.py      # 🟢 Yahoo / ETFDB（公开可用） + Polygon / AV（付费可选）
│   │
│   ├── schemas/                    # 🟢 M2 · 报告结构 Schema
│   │   ├── __init__.py
│   │   └── etf_schema.py           # 🟢 DEFAULT_REPORT_SECTIONS (10 节) + PeerResolver 4 步法
│   │
│   ├── citations/                  # 🟢 M3 · Citation + 时效性（方案 A 不存在的能力）
│   │   ├── __init__.py
│   │   └── parser.py               # 🟢 CitationParser 正则 [PROV-N] + FreshnessValidator 策略表
│   │
│   ├── exporter/                   # 🟢 M4 · 4 格式导出（B1 三位置免责 + B2 source_tag）
│   │   ├── __init__.py
│   │   └── report_exporter.py      # 🟢 ReportExporter 7 方法（JSON/MD/HTML + 4×CSV）
│   │
│   ├── cache/                      # 🟢 增强② · 结果缓存（防 429）
│   │   ├── __init__.py
│   │   └── result_cache.py         # 🟢 ResultCache（24h TTL · 空值 1h · key=ticker_YYYYMMDD_method_args）
│   │
│   ├── compliance/                 # 🟢 B1+B2+B3 合规模块
│   │   ├── __init__.py
│   │   └── disclaimer.py           # 🟢 DisclaimerRenderer 三位置 + ComplianceGate 入口拦截
│   │
│   ├── styles/                     # 🟢 增强① · Prompt 风格切换（3 种）
│   │   ├── __init__.py
│   │   └── prompt_style.py         # 🟢 PromptStyle Enum + normalize_style() 校验
│   │
│   ├── agent/                      # 🟡 4 大 Agent + Engine + Service（继承骨架）
│   │   ├── __init__.py
│   │   ├── base.py                 # 🟡 BaseAgent（MAX_ROUNDS=3 + on_progress 回调）
│   │   └── agents.py               # 🟡 Planning/Keyword/Summary/Judge/Report 5 Agent + ETFResearchEngine + ETFService
│   │
│   └── templates/                  # 🟡 Jinja2 Prompt 模板（继承手感，Dry-Run 暂空）
│       └── style_snippets/         # 🟢 增强① · 3 风格 Prompt snippet（include 注入）
│
├── data/etf_records/               # ⚫ 运行时产物（.json / .md / .html / .csv × 4）
├── cache/                          # ⚫ 日级缓存（不上传）
├── samples/                        # 🟢 脱敏示例（最终实现后补 4 份）
├── tests/                          # 🟢 L1~L3 pytest（最终实现后补 8 个用例）
└── docs/specs/                     # 🟢 Spec + 后续架构图
    └── 2026-09-13-etf-research-agent-spec-v1.0.md
```

---




## 📚 规范约定（继承自综合案例-02，0 侵入不破坏）

1. **确定性编排 vs LLM 解耦**：所有控制流（8 阶段、MAX_ROUNDS=3、Judge 收敛）都在 `etf_engine.py` 用 Python 硬编码；LLM 只负责「关键词 / 草稿总结 / Judge 判分 / 终稿润色」，绝不让 LLM 决定流程。
2. **草稿累积不重写**：`storage.on_progress` 每节写完立即 flush 到 `.json/.html`；任何 Bocha 抓取失败不会丢之前的内容。
3. **Prompt 代码分离**：所有 Prompt 文字只写在 `backend/templates/*.jinja2`；Python 只做 `jinja2.render` 注入。
4. **路径自定位**：所有数据/缓存/模板路径用 `Path(__file__).resolve()` 计算，不依赖 CWD，所以 `cd` 到任何目录都能直接 `python /abs/path/etf-research-agent/main.py`。
5. **envify-llm 规范**：`.env` 三键位（`LLM_PROVIDER + {PROV}_API_KEY/_BASE_URL/_MODEL`），级联查找优先级：本目录 → 父目录 → `../../Week08/.env`（Philip 的主环境）；启动时 3 条安全校验（空 Key / 占位符前缀 / `sk-xxx` 示例值）。
6. **所有模块间通信只传 `backend/models.py` 中 Pydantic 对象**：禁止传内部状态，避免循环 import。
7. **降级策略**：7 种失败场景全部有兜底（完整列表见 Spec §8），至少保证：1 页 10 节标题 + 基本 Profile + 免责声明 + CSV 空壳文件。
8. **测试门禁**：L1 配置 + 合规 / L2 Schema+Citation+Exporter / L3 Provider / L4 端到端 SOXL 冒烟。**MVP 接受：L1+L2 100% + SOXL 跑完 + 7 文件 >0B + Confidence≥medium**。

---

## ✅ 已通过门禁 & 交付回顾（与 Spec §9 MVP 门禁对齐）

按 `综合案例-02 VibeCoding-Philip → etf-research-agent` 5 批次落地，Gate 0~3 全部 **100% 通过无回归**：

| Gate | 批次 | 内容 | 通过标准 | 实测结果 |
|---|---|---|---|---|
| Gate 0 | 批 0 | 脚手架 + L1 Config/Compliance 基础 | L0 编译 0 失败 · L1 7/7 | ✅ 7 / 7 |
| Gate 1 | 批 1 | Provider / Schema / Citation / Cache / Exporter 数据层 | L1+L2 22/22 | ✅ 22 / 22 |
| Gate 2 | 批 2 | 5 Agent + BochaSearch + Engine 8 阶段 + Storage + Templates 5+3 | L1+L2 42/42 无回归 | ✅ 42 / 42 |
| Gate 3 | 批 3 | Phase7 真写 7 文件 + main.py CLI + app.py 3 接口 + B1/B2/B3 真实打通 | L0 编译 0 失败 · L1+L2 42/42 · 无 FastAPI union ERROR | ✅ 42 / 42 · 0 ERROR |

> 冒烟耗时提示：L1 里的 `test_cli_b3_agree_passes_exits_0` 会真跑整条 Engine（degrade 兜底分支）+ tenacity 3 次 retry，单条 ≈ 3 分钟。日常开发建议 `pytest -k "not test_cli_b3_agree"` 排除掉（FastAPI 层 B3 放行已在 `test_api_b3_400_and_pass` 测过）。

---

## 🚀 GitHub 上传前 · 10 项自检清单（照做就不会 commit 真 Key / 大文件）

1. ✅ **根目录永远只有 `.env.example`，没有真实 `.env`**（`.gitignore` 已排除 `.env` / `.env.*`，保留 `!.env.example`）。
2. ✅ **Week08/.env / 综合案例-02 VibeCoding-Philip/.env 永远只读**，它们**不在**本仓库范围内（本仓库是 `etf-research-agent/` 子文件夹）。
3. ✅ **真 Key 不写进任何 .py / .md / .json / .csv**；可全局搜一遍：`grep -R -E "sk-[A-Za-z0-9]{20,}|your_(deepseek|qwen|openai|polygon)_key_here" --exclude-dir=.git --exclude=.env.example backend main.py samples docs . || echo "✅ 真 Key 未泄漏"`（`_key_here` 是 `.env.example` 占位，允许存在）。
4. ✅ **samples/*.{json,md,html,csv} 全部是脱敏假数据**（本仓库 samples 已用全假 SOXL/SOXX 数据，B1/B2 字段齐全）。
5. ✅ **`data/etf_records/` 与 `cache/` 真实运行产物不上传**（`.gitignore` 已排除 `/data/etf_records/R_*/` + `/cache/*.json` + `/data/etf_records/*.jsonl`）。
6. ✅ **_tmp_* 临时可视化文件已删**（综合案例-02 VibeCoding-Philip 父目录 5 个 HTML 已删）。
7. ✅ **依赖文件齐全**：`requirements.txt`（pip 能装全）· `start.sh`（一键 api / research 双模式）。
8. ✅ **README 4 章齐全**：①🛰 CLI ②🔗 API ③🧪 4 级测试 + L4 SOXL 冒烟 ④📦 samples 说明（本文件已覆盖）。
9. ✅ **Spec 文档留档**：`docs/specs/2026-09-13-etf-research-agent-spec-v1.0.md` 394 行 · 12 节齐全（设计溯源用）。
10. ✅ **4 级测试入口能跑**：
    ```bash
    # L0+L1 快（≈5s）
    python3 -m compileall -q backend main.py tests
    python3 -m pytest tests/test_l1_*.py -k "not test_cli_b3_agree" -q
    # L1+L2 全（≈6:30，含 CLI 真跑 degrade 分支）
    python3 -m pytest tests/test_l1_*.py tests/test_l2_*.py -q
    ```

---

## 📜 License

推荐 MIT：MVP 为个人教学+GitHub 展示项目，允许商用/修改/分发，附带免责声明即可。正式上传 GitHub 前新增 `LICENSE` 即可。

---

---

## 🔭 Version 1 后续优化方向（Roadmap · 你将在 v1.1 / v1.2 看到）

> **本项目是 v1.0 首发版本，绝对不是终点**。在你真实使用 L4 SOXL 冒烟、或者用更多 ETF（例如 QQQ/SPY/ARKK/债券 ETF TLT/商品 ETF GLD）压测后，我们将按以下 3 条主线继续迭代：

| 优化方向 | v1.0 现状 | v1.1 / v1.2 目标 |
|---|---|---|
| **① 分析框架深度** | 10 节模板 + 5 Agent 单轮；结论基于 Provider 结构化字段 + LLM 终稿生成，同行对比矩阵仅覆盖 12 项基本指标 | 新增：① 宏观 β 因子面板（利率/利差/VIX/美元指数相关性）② 风格暴露（大小盘/成长价值/质量因子）③ 同类 ETF 归因拆解（主动收益 = β 贡献 + α 残差）④ 情景分析（加息 25bp / 科技下跌 10% 压力测试） |
| **② Confidence 置信度算法** | 目前：规则表 heuristic（缺失节数 × stale_warnings 条数 + citations 计数，3 档 cut），和 LLM Judge 松耦合 | 升级：① 多信号加权打分（数据新鲜度 / Provider 数量 / 引用覆盖率 / 同行矩阵完整度 / Judge 自评分 / Prompt Style 一致性）② 校准曲线：用 20 只 ETF 真值回测 ≥medium 的精确率 ≥0.8 ③ 新增「置信度构成说明」一节，明确写清每个 signal 扣了多少分 |
| **③ 响应速度 & 交互** | 同步串行：Provider fetch（Yahoo→ETFDB→…）→ Phase3 Planning → Phase4 Bocha → Phase5 Summary → Phase6 Judge → Phase7 Export，单轮 ≈ 3~10 min | 升级：① **Provider 层 httpx async + as_completed**：Yahoo profile/history/risk 3 个接口并发；② Bocha 关键词 n 条并发搜；③ Summary/Judge Agent 流式返回（CLI 进度条 + SSE）；④ 新增 resume：Engine 每 phase 结束即 flush，再次运行同样 ticker 自动从最近已完成 phase 续跑，无需重新开始 |

> 回复你希望优先推进哪条线（例如「v1.1 先做 ③ 响应速度」），我立即按 TRAE-plan-mode 重新起草 v1.1 Spec + 分批实现。

---

## 📚 参考

- 设计基底：Week8-agent 综合案例-02（目录：`../../综合案例-02/`）
- 设计文档：[docs/specs/2026-09-13-etf-research-agent-spec-v1.0.md](file:///Users/philipclaw/Downloads/padow-ai/Week8-agent/Week08/Part2-VibeCoding实操/综合案例-02%20VibeCoding-Philip/etf-research-agent/docs/specs/2026-09-13-etf-research-agent-spec-v1.0.md)
