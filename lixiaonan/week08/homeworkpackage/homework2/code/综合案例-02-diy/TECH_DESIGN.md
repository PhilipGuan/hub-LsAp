# 技术方案 · 深度研究助手（Deep Research Assistant）

| 项 | 内容 |
| --- | --- |
| 文档版本 | v0.1（草案） |
| 依据 | PRD.md v0.1 |
| 技术栈基线 | Python 3.12 · 阿里云百炼 DashScope（OpenAI 兼容模式，与课程 Part1 保持一致） |

---

## 1. 技术选型

| 层 | 选型 | 理由 |
| --- | --- | --- |
| 语言 | Python 3.12 | 课程基线 |
| LLM | `openai` SDK + DashScope 兼容模式（`base_url=https://dashscope.aliyuncs.com/compatible-mode/v1`），主力 `qwen-plus`，规划/综合用 `qwen-max` | 与课程代码同一套接入方式；兼容模式可直接复用 `openai` SDK |
| 搜索 | 博查 Web Search API（`POST https://api.bocha.cn/v1/web-search`，`summary: true, count: 10`） | PRD 指定；`summary=true` 直接返回网页摘要，可省一次全文抓取 |
| 网页阅读 | P0：博查 `summary` 字段；P1 增强：`httpx` 抓 URL + `trafilatura` 抽正文 | P0 不引入额外依赖即可跑通闭环 |
| 结构化输出 | `pydantic` v2 校验 LLM 的 JSON 输出 | 保证"规划/抽取/判断/综合"每一步输出可校验、可重试 |
| 配置 | `python-dotenv` + 环境变量 | 密钥不入库 |
| Agent 框架 | **不引入** LangGraph/AutoGen，手写主循环 | 综合案例目标是看懂 Agent 原理：循环 + 工具 + 判断，代码量约 400 行 |

**依赖清单（requirements.txt）**：`openai`、`httpx`、`pydantic`、`python-dotenv`、`trafilatura`（P1 可选）。

## 2. 总体架构

```
┌──────────────────────────────────────────────────┐
│ 入口层  main.py（CLI：python -m research "主题"）   │
├──────────────────────────────────────────────────┤
│ 编排层  agent.py  ← 核心：研究主循环                 │
│   plan → (search → read → reflect)×N → synthesize │
├──────────────┬───────────────────────────────────┤
│ LLM 适配层    │ 工具层                             │
│ llm.py       │ tools/search.py  博查 Web Search   │
│ chat_json()  │ tools/reader.py   网页摘要/正文抽取 │
│ 重试+JSON校验 │                                   │
├──────────────┴───────────────────────────────────┤
│ 数据层  models.py（Pydantic）· prompts.py          │
│ 输出层  reporter.py → 四件套落盘                    │
└──────────────────────────────────────────────────┘
```

模块职责单一，`agent.py` 只做编排，不写业务提示词；`tools/` 不依赖 LLM，可独立单测。

## 3. 核心设计：来源登记表（Source Registry）

这是满足 PRD"每条结论可追溯 ≥80%"的关键机制，贯穿全流程：

1. 每接触一个新 URL，登记为一条来源，分配 `sid`（S1、S2…）。
2. 阅读抽取产出的每条**材料（Finding）**必须携带 `source_ids`。
3. 综合生成时，要求 LLM 在报告中用 `[S1][S3]` 编号引用材料来源。
4. 渲染时把编号映射回"来源列表"（URL / 标题 / 摘要）。
5. 模型输出中**没有来源编号支撑的结论**，强制标注"模型推断"（渲染层兜底校验，不信任 LLM 自觉）。

## 4. Agent 主循环设计

伪代码（`agent.py`）：

```python
def run(topic: str) -> ResearchResult:
    plan = plan_topic(topic)                       # ① 规划：拆 3~6 个子问题
    registry = SourceRegistry()
    trace = TraceLogger()

    for round_no in range(1, cfg.MAX_ROUNDS + 1):  # ②③④ 迭代
        pending = [q for q in plan if q.status == "pending"]
        if not pending:
            break
        for sub in pending:
            keywords = gen_keywords(sub, registry, trace)     # 生成检索词
            hits = web_search(keywords)                       # 调博查
            for hit in dedup(hits)[: cfg.PAGES_PER_ROUND]:
                finding = extract_finding(hit, sub)           # 阅读抽取 → 材料
                registry.register(hit)                        # 登记来源
            sub.status = judge_subquestion(sub, findings)     # 反思：解决/需补检/放弃
        if all_resolved_or_budget_exhausted(plan):
            break

    report = synthesize(topic, plan, registry.findings)     # ⑤ 综合
    write_outputs(report, registry, trace)                  # 四件套落盘
```

各阶段说明：

| 阶段 | 输入 | LLM 任务 | 输出（JSON Schema） |
| --- | --- | --- | --- |
| ① 规划 | 研究主题 | 拆 3~6 个可检索的子问题 | `Plan{subquestions:[{id, question}]}` |
| ② 关键词 | 子问题 + 已搜过的词（避免重复） | 生成 2~3 个差异化检索词 | `Keywords{queries:[...]}` |
| ③ 抽取 | 搜索摘要 / 网页正文 + 子问题 | 抽取与子问题相关的事实，**只准转述材料内容** | `Finding{content, source_ids, related_sub_id, confidence}` |
| ④ 反思 | 子问题 + 该子问题已有材料 | 判定：`resolved`（材料充分）/ `need_more`（给出补检方向）/ `abandoned`（搜不到，如实记录） | `Judgement{sub_id, status, reason, extra_keywords}` |
| ⑤ 综合 | 全部子问题 + 材料 + 来源登记表 | 生成报告，结论必须带 `[Sn]` 引用 | `Report{title, summary, sections[], key_conclusions[], open_questions[], confidence_notes[], info_cutoff}` |

**轮次终止条件**（满足其一）：全部子问题 resolved/abandoned；达到 `MAX_ROUNDS`；总页面数达 `MAX_TOTAL_PAGES`。

## 5. 数据结构（models.py，节选）

```python
class Source(BaseModel):
    sid: str          # "S1"
    url: str
    title: str
    snippet: str = ""

class Finding(BaseModel):
    content: str                  # 事实/观点转述
    source_ids: list[str]         # 关联来源，空 = 模型推断
    related_sub_id: str
    confidence: Literal["high", "medium", "low"]  # 多来源一致/单来源/弱证据

class SubQuestion(BaseModel):
    id: str
    question: str
    status: Literal["pending", "resolved", "abandoned"] = "pending"
    keywords_tried: list[str] = []

class Report(BaseModel):
    title: str
    summary: str
    sections: list[Section]          # heading + body + refs
    key_conclusions: list[Conclusion]  # text + refs + confidence
    open_questions: list[str]          # 遗留问题
    confidence_notes: list[str]        # 置信度说明
    info_cutoff: str                   # 信息截止时间

class TraceEvent(BaseModel):           # 过程记录，逐条追加 trace.jsonl
    round_no: int
    action: Literal["plan", "search", "read", "reflect", "synthesize"]
    detail: dict                       # 关键词 / URL / 判定理由等
```

## 6. LLM 适配层（llm.py）

- `chat_json(system, user, schema) -> BaseModel`：
  1. DashScope 兼容模式开启 `response_format={"type": "json_object"}`；
  2. 返回值经 pydantic 校验，**校验失败自动把错误信息回喂重试（最多 2 次）**；
  3. 仍失败 → 抛出并由上层降级（如该子问题标记 abandoned）。
- 超时 60s，指数退避重试 3 次（429/5xx）。
- 密钥与 base_url 全部走环境变量（课程示例代码中有明文 key，本项目一律改为 `.env`）。

## 7. 工具层

**search.py（博查）**
- 请求：`POST /v1/web-search`，`{query, summary: true, count: 10}`，`Bearer <BOCHA_API_KEY>`。
- 响应解析出 `title / url / summary` → `SearchHit` 列表。
- URL 规范化去重（去 utm_* 等跟踪参数）。
- 失败处理：超时重试 2 次；仍失败则该关键词记入 trace，继续下一个，不中断整轮。

**reader.py**
- P0：直接使用博查 `summary` 作为阅读材料。
- P1：对 `confidence` 存疑的关键页面抓取全文（`httpx` + `trafilatura`，截断至 ~4000 字），提高引用质量。

## 8. 成本与上限控制（config.py）

| 配置 | 默认值 | 说明 |
| --- | --- | --- |
| `MAX_ROUNDS` | 4 | 检索-反思最多迭代轮次 |
| `PAGES_PER_ROUND` | 5 | 每子问题每轮最多细读页面数 |
| `MAX_TOTAL_PAGES` | 25 | 单次研究总页面上限 |
| `SEARCH_COUNT` | 10 | 博查每次返回条数 |
| `MAX_SUB_QUESTIONS` | 6 | 规划拆解上限 |

到限即强制进入综合阶段，遗留信息缺口如实写入"遗留问题 / 置信度说明"。

## 9. 输出物落盘（reporter.py）

```
output/20260909-1740_競品分析-XX品類/
├── report.md    # ①报告(摘要/分节正文/关键结论/遗留问题) ②来源列表 ④置信度说明
└── trace.jsonl  # ③研究过程记录（逐事件追加，可回放审计）
```

- `report.md` 结构：`# 标题 → ## 摘要 → ## 正文（分节，行内 [Sn] 引用）→ ## 关键结论（每条带 refs + 置信度）→ ## 遗留问题 → ## 置信度说明 → ## 来源列表（Sn | 标题 | URL）`。
- 渲染层兜底：校验每条结论的 refs 非空，否则自动追加"（模型推断）"标记。
- `info_cutoff`：取来源中可识别的最新时间；均不可得则写"未知，建议核实时效"。

## 10. 错误处理与边界

| 场景 | 处理 |
| --- | --- |
| 博查超时/限流 | 指数退避重试 2 次 → 跳过该关键词，记录 trace |
| 网页摘要为空/抓取失败 | 跳过并记入 trace；该 URL 不进来源列表 |
| LLM JSON 不合法 | 错误回喂重试 2 次 → 子问题降级 abandoned |
| 全部搜索失败 | 终止流程，输出仅含"研究失败说明"的报告，不产出无据结论 |
| 主题过宽 | 规划阶段强制收敛为 ≤6 个子问题 |
| 中文编码 | httpx 强制 `follow_redirects=True`、按响应头/嗅探解码 |

## 11. 目录结构

```
综合案例-02-diy/
├── README.md / PRD.md / TECH_DESIGN.md
├── .env.example          # DASHSCOPE_API_KEY / BOCHA_API_KEY / LLM_MODEL
├── .env                  # 本地密钥（gitignore）
├── requirements.txt
├── research/
│   ├── main.py           # CLI 入口与参数解析
│   ├── agent.py          # 主循环编排
│   ├── llm.py            # LLM 适配（chat_json）
│   ├── config.py         # 上限配置 + 环境变量
│   ├── models.py         # Pydantic 数据结构
│   ├── prompts.py        # 5 个阶段提示词（集中管理）
│   ├── tools/
│   │   ├── search.py     # 博查封装
│   │   └── reader.py     # 网页阅读（P1 全文抽取）
│   └── reporter.py       # 四件套渲染落盘
├── tests/
│   ├── test_search.py    # mock 博查响应
│   ├── test_registry.py  # 来源登记/去重/引用映射
│   └── test_agent.py     # mock LLM 的主循环状态机
└── output/               # 运行产物（gitignore）
```

## 12. 测试方案

1. **单测**（不触网）：mock 博查响应 → 解析/去重正确；mock LLM → 主循环状态机（resolved/need_more/abandoned 分支）、来源编号映射、"模型推断"兜底标注。
2. **联调冒烟**：真实跑 1 个主题（如"AI 眼镜 2026 年竞争格局"），检查四件套完整性。
3. **验收对照**（对应 PRD 第 7 节）：结论带源率 ≥80%；trace 可还原关键词/页面/轮次；耗时分钟级。

## 13. 实施里程碑

| 里程碑 | 内容 | 验收 |
| --- | --- | --- |
| M1 | 配置 + LLM 适配层 + 博查工具封装 | 单独调通"关键词 → 搜索结果列表" |
| M2 | 规划 + 单轮"检索→抽取→来源登记" | 一个子问题产出带 sid 的材料 |
| M3 | 反思判断 + 多轮主循环 + 上限控制 | 多轮迭代正确终止 |
| M4 | 综合生成 + reporter 四件套落盘 | 主题端到端产出报告 |
| M5 | 单测 + 冒烟 + 验收对照 | PRD 第 7 节全部通过 |

## 14. 风险与应对（技术视角）

| 风险 | 应对 |
| --- | --- |
| LLM 引用编号与登记表不一致（幻觉引用） | 渲染层校验：refs 中不存在的 sid 直接剔除并降级为"模型推断" |
| 博查 summary 偏短、材料单薄 | P1 启用全文抓取增强；提高 SEARCH_COUNT |
| qwen JSON 输出不稳定 | `chat_json` 统一收口重试 + pydantic 兜底 |
| token 成本 | 材料截断（每条 ≤500 字）、页面数上限、`qwen-flash` 跑抽取、`qwen-max` 只跑规划/综合 |
| 上下文超长 | 综合阶段按子问题分组投喂材料，而非一次性全量 |

---

*备注：本方案依据 PRD.md 设计，与课程 Part1 的 DashScope/OpenAI 兼容接法保持一致；未采用重框架，主循环手写以便教学讲解。*
