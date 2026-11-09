# 开发任务拆解 · 深度研究助手

| 项 | 内容 |
| --- | --- |
| 依据 | TECH_DESIGN.md v0.1（里程碑 M1~M5） |
| 总量 | **9 个开发步骤**，按依赖顺序串行推进（S1~S4 为地基，S6 为核心） |

---

## 步骤总览

| 步骤 | 名称 | 产出文件 | 对应里程碑 | 预估量 |
| --- | --- | --- | --- | --- |
| S1 | 项目骨架与环境配置 | 目录结构、`requirements.txt`、`.env.example`、`.gitignore`、`config.py` | M1 | 小 |
| S2 | 数据结构与来源登记表 | `models.py` | — | 中 |
| S3 | LLM 适配层 | `llm.py` | M1 | 小 |
| S4 | 博查搜索工具 | `tools/search.py` | M1 | 小 |
| S5 | 五阶段提示词 | `prompts.py` | — | 中 |
| S6 | Agent 主循环（核心） | `agent.py` | M2 + M3 | 大 |
| S7 | 综合生成与四件套落盘 | `reporter.py` | M4 | 中 |
| S8 | CLI 入口端到端串联 | `main.py` | M4 | 小 |
| S9 | 测试与验收 | `tests/` + 冒烟记录 | M5 | 中 |

**依赖关系**：S1 → S2 → {S3, S4}（可并行）→ S5 → S6 → S7 → S8 → S9

---

## S1 项目骨架与环境配置

- 建目录 `research/`、`research/tools/`、`tests/`、`output/`
- 写 `requirements.txt`：`openai / httpx / pydantic / python-dotenv`
- 写 `.env.example`（`DASHSCOPE_API_KEY / BOCHA_API_KEY / LLM_MODEL`），`.gitignore` 排除 `.env` 与 `output/`
- `config.py`：读取环境变量 + 上限常量（`MAX_ROUNDS=4`、`PAGES_PER_ROUND=5`、`MAX_TOTAL_PAGES=25`、`SEARCH_COUNT=10`、`MAX_SUB_QUESTIONS=6`）

✅ **完成标准**：`pip install -r requirements.txt` 成功；脚本能从 `.env` 读到两个密钥。

## S2 数据结构与来源登记表

- `models.py`：`Source / Finding / SubQuestion / Plan / Report / TraceEvent` 等 Pydantic 模型
- 实现 `SourceRegistry`：登记来源、分配 sid、URL 规范化去重（去 `utm_*`）、按子问题取材料
- 实现 `TraceLogger`：逐事件追加 `trace.jsonl`

✅ **完成标准**：注册重复 URL 只得一个 sid；`Finding` 无 `source_ids` 时能被识别为"模型推断"。

## S3 LLM 适配层

- `llm.py` 的 `chat_json(system, user, schema)`：
  - DashScope 兼容模式 + `response_format={"type":"json_object"}`
  - pydantic 校验失败 → 错误回喂重试 2 次；超时/429/5xx → 指数退避 3 次

✅ **完成标准**：单独脚本让 qwen 输出符合 `Plan` schema 的 JSON 并校验通过。

## S4 博查搜索工具

- `tools/search.py`：`POST /v1/web-search`（`summary:true, count:10`）→ 解析 `title/url/summary` → `SearchHit`
- 超时重试 2 次，仍失败记入 trace 并跳过（不中断整轮）

✅ **完成标准**（M1 验收）：真实调用"关键词 → 去重后的搜索结果列表"跑通。

## S5 五阶段提示词

- `prompts.py` 集中管理：规划 / 关键词生成 / 阅读抽取 / 反思判断 / 综合生成
- 每个 prompt 内嵌对应 JSON schema 说明与硬约束（只准转述材料、结论必须带 `[Sn]`、无来源标"模型推断"、中文输出）

✅ **完成标准**：5 个 prompt 逐一用 S3 的 `chat_json` 试跑，输出均可被校验。

## S6 Agent 主循环（核心步骤）

分三个子任务递进：

1. **单轮打通**：`规划 → 生成关键词 → 搜索 → 抽取 Finding → 登记来源`
2. **反思与多轮**：`judge_subquestion`（resolved / need_more / abandoned）+ 循环直到终止条件（全部解决、达 `MAX_ROUNDS`、达 `MAX_TOTAL_PAGES`）
3. **上限与降级**：到限强制进入综合；LLM JSON 失败 → 子问题降级 abandoned

✅ **完成标准**（M2+M3 验收）：一个子问题产出带 sid 的材料；多轮迭代能正确终止。

## S7 综合生成与四件套落盘

- `reporter.py`：
  - 组装材料投喂综合 prompt（按子问题分组，避免上下文超长）
  - 渲染 `report.md`：摘要 → 分节正文（行内 `[Sn]`）→ 关键结论（refs + 置信度）→ 遗留问题 → 置信度说明 → 来源列表
  - **兜底校验**：refs 为空自动标"（模型推断）"；不存在的 sid 剔除降级
  - 产出目录 `output/<时间戳_主题>/`（`report.md` + `trace.jsonl`）

✅ **完成标准**（M4 验收）：给定主题端到端产出完整四件套。

## S8 CLI 入口串联

- `main.py`：`python -m research "主题"` → 调 `agent.run()`，控制台打印进度（当前轮次/子问题/已读页面数）

✅ **完成标准**：一条命令从输入主题到产出文件，无需人工干预。

## S9 测试与验收

- 单测（不触网）：mock 博查响应 → 解析/去重；mock LLM → 主循环三态分支、sid 映射、"模型推断"兜底
- 联调冒烟：真实主题（如"AI 眼镜 2026 年竞争格局"）跑一遍
- 验收对照 PRD 第 7 节：**结论带源率 ≥80%**、trace 可还原过程、总耗时分钟级

✅ **完成标准**（M5 验收）：单测全绿 + 冒烟产物符合验收三条。

---

## 风险提示（开发顺序相关）

- **S6 是风险集中区**（迭代终止、JSON 降级、上下文长度都在这里），预留最多时间；S3/S5 的质量直接决定 S6 顺不顺。
- 建议 S1~S5 完成后就做一次"单子问题手工串联合测"，再进 S6，避免最后才端到端联调。
- `tools/reader.py`（全文抓取增强）为 P1，放到 S9 之后作为独立迭代，不阻塞主线。
