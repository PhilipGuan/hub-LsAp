# 综合案例 02 · 深度研究助手

## v0.1 · 需求

**业务背景**：市场 / 产品同学经常要对一个主题做调研（竞品分析、行业趋势、技术选型、政策解读）。人工搜索几十个网页、整理资料、写报告，一个主题动辄 2~3 小时，还容易漏信息、来源不可追溯。希望有一个工具能自动完成**深度研究**：输入一个主题，自动检索、阅读、迭代、综合，最终产出一份带来源引用的研究报告。

**产品定位**：「深度研究助手」——输入一个研究主题，输出：

1. 一份**结构化研究报告**（摘要、分节正文、关键结论、遗留问题）
2. **来源列表**（每条结论关联 URL / 标题 / 来源，可追溯）
3. **研究过程记录**（检索了哪些关键词、读了哪些页面、迭代了几轮）
4. **置信度说明**（结论的可靠程度、信息截止时间、无来源结论标注为"模型推断"）

**核心流程**（区别于一次性问答）：规划（拆子问题）→ 多轮检索 → 阅读抽取 → 判断是否需要补检 → 综合生成报告。

# 搜索工具

https://bocha-ai.feishu.cn/wiki/RXEOw02rFiwzGSkd9mUcqoeAnNK

```
curl -X POST "https://api.bocha.cn/v1/web-search" \
  -H "Authorization: Bearer $BOCHA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query":"天空为什么是蓝色的？","summary":true,"count":10}'
```

---

## v0.2 · 设计定稿（2026-09-10，经三轮设计评审确认）

### 1. 形态与技术基线

- **交付**：FastAPI 后端 + Next.js 单页前端；根目录 `start.sh` 一键拉起（后端 :8000 / 前端 :3000）。
- **LLM 接入**：OpenAI 兼容协议**裸 HTTP 实现**，不绑任何厂商 SDK / Agents SDK。环境变量 `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` 三项配置，解析 `choices[0].message.content`。
- **编排**：**多角色 prompt + Python 确定性编排**。循环控制、工具调用、并发全部由代码驱动，每个角色是一次独立的 LLM 调用（无 tool-calling）。
- **执行模式**：后台任务。`POST /api/research` 立即返回任务 id，`GET /api/research/{rid}` 轮询进度与产物；中间结果随执行逐步更新落盘。
- **存储**：JSON 文件落盘 `data/{rid}.json`（一个研究一个文件，git 忽略）。不上数据库。
- **环境**：后端 conda `py312`（本机 shell 用 `python` 命令，`python3` 是 Windows Store stub 不可用）；node v24 / npm。`.env` 忽略、`.env.example` 入库。

### 2. 研究引擎（核心流水线）

```
Planner（1 次 LLM 调用）
  → { topic_understanding, sub_questions[3~5]{id, question, suggested_queries[2~3]},
      report_outline_hint }
  ↓
研究循环（≤ MAX_ROUNDS=3 轮；轮内多词搜索 + 多页抓取用 asyncio.gather 并发）
  ├─ 搜索   ：每个检索词一次 Bocha POST /v1/web-search，count=10，
  │           取 data.webPages.value[]（name/url/snippet/summary/siteName/dateLastCrawled）
  ├─ 抓取   ：按 URL 全程去重，每轮抓 top 5 页 → requests 拉 HTML
  │           → BeautifulSoup 去噪（去 script/style/nav 等）→ 截断 8000 字符
  ├─ 阅读抽取：每页 1 次 LLM 调用 → { url, key_points[], not_relevant? }
  │           （溯源在阅读时建立；单页失败跳过，不影响整体）
  └─ Judge  ：逐子问题评估覆盖度
              → { sub_question_coverage[{question, covered, evidence_count}],
                  sufficient, missing_aspects[], new_queries[2~4] }
              硬条件（代码判定）：来源总数 < 5 或存在 0 覆盖子问题 → 强制 insufficient
  ↓ sufficient 或轮数用尽
Writer（1 次 LLM 调用）：子问题 + 全部页面要点 + coverage → 四类产物
```

- **检索词语言**：跟随主题语言为主，允许 Planner/Judge 混合输出中英检索词。
- **默认参数**（全部 `.env` 可覆盖）：`MAX_ROUNDS=3`、每轮新检索词 2~4 个、`count=10`（免费额度上限）、每轮抓 top 5 页 / 全程 ≤15 页、正文截断 8000 字符。
- **容错**：单条搜索 / 单页抓取失败 → 记入过程记录并跳过，不中断整体；LLM 返回非 JSON → fence 容错解析 + 重试 3 次，仍失败则该角色降级或任务标 failed。

### 3. 产物契约（实现必须遵守）

**① 结构化报告**
```json
{
  "title": "...",
  "summary": "...",
  "sections":   [{ "title": "...", "content": "...", "source_ids": ["S1","S3"] }],
  "key_conclusions": [{ "text": "...", "source_ids": ["S1"], "confidence": "high|medium|low|inferred" }],
  "open_questions": ["..."]
}
```
- `source_ids` **两级绑定**：每节正文（该节整体依据）+ 每条关键结论（精确溯源）。遗留问题不绑定。
- 生成后用代码校验：所有 `source_ids` 必须存在于来源列表，否则降级/剔除。

**② 来源列表**：`[{ id, url, title, site_name, snippet, accessed_at, date_last_crawled }]`

**③ 过程记录**：`{ plan, rounds[{queries, found_urls, read_pages, coverage_snapshot}], iterations }`

**④ 置信度说明（混合模式）**
- Writer 在结构化输出中给每条结论初步评级；
- Python 规则校验/降级：佐证来源 < 2 条 → 降为 `low`；无来源 → 强制 `inferred`（"模型推断"）；
- 全局信息截止时间 = 所有来源 `dateLastCrawled` 的最大值；
- 每条结论可解释"为什么是这个等级"。

### 4. API 契约

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/research` | 入参 `{"topic": "..."}`，202 返回 `{"research_id", "status"}` |
| GET | `/api/research/{rid}` | 返回 status + 进度 + 完成后四类产物 |
| GET | `/api/research` | 历史任务列表（读 `data/` 落盘文件） |
| DELETE | `/api/research/{rid}` | 删除该研究的落盘文件 |

另设 `GET /health`。

### 5. 前端

三个视图（Next.js 单页应用）：
1. **发起页**：输入主题 → 提交 → 跳转详情页
2. **详情页**：轮询进度（轮次时间线、已检词、已读页面、coverage 状态）；完成后按**结构化 JSON 渲染**四类产物——关键结论的 `source_ids` 可点击滚动到来源列表
3. **历史任务列表页**：读后端列表接口

### 6. 工程约定

- **测试**：pytest 最小单测，只覆盖纯函数——JSON fence 容错解析、HTML 去噪截断、置信度规则降级、来源去重（不 mock 外部 API）；另加 `scripts/smoke.py` 手动全链路冒烟。
- **密钥**：`BOCHA_API_KEY` 与 LLM 三项全部在根目录 `.env`；文档中只留占位符。
- **默认执行项**：与 LLM 的交互默认中文；`open_questions` = Judge 的 `missing_aspects` + Writer 自行补充（合并）；**不做** HTML 报告导出（前端渲染替代）；进度中间结果每轮实时更新到 `data/{rid}.json`。
- 不做与需求无关的过度工程；与本文档不一致的取舍需显式说明。
