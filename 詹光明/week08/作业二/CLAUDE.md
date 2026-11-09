# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

「深度研究助手」：输入研究主题 → 规划子问题 → 多轮检索 → 抓取阅读 → 缺口判断补检 → 综合报告。
产出带来源引用（`[S3]`）的结构化报告，**引用可追溯**、**无来源结论显式标注「模型推断」**。

本版只实现 `backend/`（Python + FastAPI）。前端（Next.js）尚未实现，但 `app/api.py` 已按前端消费的需要定义了 SSE 事件契约。

## 常用命令

**全部命令都在 `backend/` 下执行。**

```bash
# 依赖与检查
uv sync                                  # 安装依赖
uv run pytest -q                         # 全部测试（248 个，全部离线，约 2 秒）
uv run pytest tests/test_citation.py -q  # 单个测试文件
uv run pytest tests/test_citation.py::TestProcessText::test_fabricated_sid_is_replaced -q
uv run ruff check . && uv run ruff format .

# 调试用 CLI —— 开发阶段的主要手段，不依赖 HTTP
uv run python -m app.cli search "<关键词>"                      # 只跑检索
uv run python -m app.cli search "<关键词>" --raw                # 打印原始响应，核对博查字段
uv run python -m app.cli search "<关键词>" --save-raw sample    # 存成 tests/fixtures/sample.json
uv run python -m app.cli fetch <url> [<url>...]                 # 只跑抓取 + 正文提取
uv run python -m app.cli fetch <url> --summary "..."            # 模拟博查摘要，演示降级链
uv run python -m app.cli plan "<主题>" --reflect                # 只跑规划（+ 反思）
uv run python -m app.cli run "<主题>" --max-rounds 3            # 完整链路，打印报告
uv run python -m app.cli run "<主题>" --no-fetch                # 跳过抓取，只验编排与 LLM
uv run python -m app.cli run "<主题>" --events                  # 末尾附事件流清单
uv run python -m app.cli run "<主题>" > report.md               # 进度走 stderr，stdout 是干净 markdown
```

```bash
# API 层（也在 backend/ 下启动，否则找不到 app 包）
uv run uvicorn app.api:app --reload --port 8000
curl -s localhost:8000/health

# 建任务立刻返回 run_id，研究在后台跑
curl -s -X POST localhost:8000/api/research \
     -H 'Content-Type: application/json' \
     --data-binary @req.json          # req.json: {"topic": "...", "max_rounds": 3}

curl -N localhost:8000/api/research/<run_id>/events        # SSE，事件随发生随到
curl -N "localhost:8000/api/research/<run_id>/events?since=40"   # 断线重连：只补 seq>40
curl -s localhost:8000/api/research/<run_id>               # 进度快照
curl -s localhost:8000/api/research/<run_id>/report        # 报告 JSON；未完成时 409
curl -s -X POST localhost:8000/api/research/<run_id>/cancel
```

> Windows 上用 `curl -d '{"topic":"中文"}'` 会因控制台代码页把中文弄坏，报
> `There was an error parsing the body`。把 JSON 写进文件再 `--data-binary @file`。

需要 `.env`（从 `.env.example` 复制）：`BOCHA_API_KEY`、`LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`。
`LLM_API_KEY` 为空时整条链路仍会跑完并出报告，只是所有 LLM 环节降级（planner 走模板、
reader 抽不出笔记、置信度被 `CAP_NO_CONCLUSIONS` 压到 0.30）——**不会崩，也不会假装有产出**。

## 架构

### 编排是唯一的真相源

`app/engine.py` 是一个**显式 async 状态机**，写成异步生成器：

```python
async def run_research(req: ResearchRequest) -> AsyncIterator[Event]:
```

**刻意不引入 LangGraph / LangChain**：全流程只有 5 个节点加一个循环，图抽象是净负担。且异步生成器让 CLI 和 SSE 共用同一份实现——`cli.py` 就是 `async for ev in run_research(req)` 打印，`api.py` 就是 `async for ev in ...: yield sse(ev)`。**改流程只改 engine 一处。**

状态流转：`INIT → PLANNING → SEARCHING → FETCHING → READING → REFLECTING →`（回 SEARCHING 或）`→ SYNTHESIZING → DONE`。

**测试全程不联网，这是刻意维持的性质，而且由 `tests/conftest.py` 强制执行。** `run_research(...)` 的 `searcher` / `llm` / `fetcher` 三个依赖都可注入，`test_engine.py` 用假替身把整条状态机在毫秒内跑穿；`api.py` 对应的钩子是 `RunRegistry(runner=...)`。加新功能时请沿用这个注入点。

conftest 里那个 autouse 夹具会把 `httpx` 的 `send` 堵死，任何真实请求直接报错（万不得已要联网的用例加 `@pytest.mark.allow_network` 放行）。这道闸不是多余的：`test_missing_api_key_raises` 早先写的是 `LLMClient()`，读的是真实 `.env`，在 `LLM_API_KEY` 为空时「碰巧」通过 —— 一旦填上真 key 它就会去打 DeepSeek。**测试依赖本机环境是最糟的一种坏法**：本机绿、别人机器红，而且不联网这条性质已经悄悄没了。

**引擎不把异常漏给调用方**：`CancelledError` 先吐 `cancelled` 事件再重抛，其它异常记日志、置 `failed`、吐一条 `fatal=True` 的 `error` 事件。所以调用方永远拿到的是一个"跑完了"的流，而不是半路炸掉。

### 引用可追溯（本项目核心，改这块前务必读 `app/citation.py`）

来源编号 `S1..Sn` 在**检索去重通过时立即分配**，全局唯一、永不复用、永不重排。编号随 `Source.sid` 一路传递：注入 reader 的 prompt → 进入 `Note.sid` → 进入 synthesizer 的可用来源列表 → 报告正文写 `[S3]`。

**不能信任 LLM 输出的引用**。`citation.py` 做后处理校验：正则扫出所有 `[Sn]` 与来源表比对，未知编号按比例决定「替换为（引用缺失）」还是「触发一次修复调用」；零引用的句子由程序强制加 `⚠️ 模型推断：` 前缀——**程序判定优先于 LLM 自述**。

**来源表的「引用」列必须区分选择和失败**（`report.py`）。一条来源抽到 0 条笔记时，它对结论的贡献是零；早先这种情况和「读了但没引用」一样渲染成 `— 已读未引`，读起来像「读过没用上」——**把失败伪装成选择，等于伪造了来源的可信度**。所以 `SourceRef` 带 `note_count` 和 `readable`，三态分开：`✅` 被引用 / `— 已读未引`（抽到了笔记，是选择）/ `⚠️ 阅读未抽出笔记`（送读了但失败）/ `— 未参与阅读`（重复来源或只剩片段，压根没送去读）。

### 置信度是算出来的，不是 LLM 说的

`app/confidence.py` 纯代码计算（来源数、域名多样性、权威性、时效、佐证度、抓取成功率、引用覆盖率七项加权），并施加硬上限（如 `n<3` 总分封顶 0.35）。LLM **只写定性局限说明，不参与打分**。

关键概念 `Source.content_origin` ∈ `page | search_summary | snippet`：区分「真读到网页正文」还是「降级用搜索摘要」。它既参与置信度打分，也是验收「是不是真深度研究」的依据。

**两个跑出来的实情，调参前先读这段**（四个主题实测的结论，不是推测）：

- **`stop_reason` 几乎总是 `budget_exceeded`，`sufficient` 实际不可达。** 两个独立原因叠在一起：
  1. `max_sources=30` 和 `queries_per_round=3 × search_count_per_query=10` 打架 —— 一轮检索去掉重复就能进 10~13 条，两轮顶到 30。`budget_exceeded` 在**第 2 轮末尾**就命中，第 3 轮永远跑不到，`max_rounds_reached` 因此也见不着。
  2. 更根本的是 **reflector 从来没说过 `sufficient=True`** —— 四个主题 × 每轮一次反思，**8 次全是否**（日志里 `LLM 认为材料已足够，但…` 这条 INFO 一次都没出现，说明不是「说够了但被覆盖率否掉」）。`stopping.py` 里 `sufficient` 排在**第一个**判定，但它是三条件合取，LLM 那一关就过不去。

  四个主题（含专为「冷门」挑的 C）**全部**在 2 轮死于 `budget_exceeded`，`sufficient` / `no_new_sources` / `max_rounds_reached` 一次都没触发过。**想看到其它收尾原因，先调大 `max_sources`**（比如 60），否则加判断逻辑是白加。
- **`时效性` 因子普遍是 0.00，而报告仍会印「信息截止时间」。** 这两个数来自不同的统计量：`as_of` 取 `max(published_at)`（最新一条），时效因子取**中位数**年龄。博查索引里大量条目是 2022~2024 年的，中位年龄 530~998 天，超过 `freshness_window_days=365` 就归零。所以会出现「报告说信息截止 2026-08-20，时效得分 0.00」这种看起来自相矛盾的组合 —— **不是 bug，但报告里那句「信息截止时间」容易被读成「材料很新」**，它其实只说明「最新的一条是这个日期」。

### 外部依赖的已知坑（踩过的）

- **博查搜索**：响应路径 `data.webPages.value[]`，字段 `name/url/snippet/summary/siteName/datePublished`。**该结构来自第三方文档，未经官方一手确认** —— `search.py` 用多路径容错解析，解析出 0 条时打 WARNING 输出实际顶层 key。`uv run python -m app.cli search "测试"` 可随时核对。
- **DeepSeek 结构化输出**：只支持 `response_format={"type":"json_object"}`，**不支持 `json_schema`**（会 400）。已知会返回空 content、```json 包裹、字段类型漂移。`llm.py` 的三层修复链（清洗围栏 → JSON 提取重试 → Pydantic 宽容转换）是必需品不是优化项。
- **推理模型的 `reasoning_tokens` 也算进 `max_tokens`** —— 这条实测代价很大。`deepseek-flash` 读一篇 4500 字的网页，光思考就吃满 3072 的预算（`finish_reason=length`，`content` 为空），于是这一页抽 0 条笔记，而日志只说「返回了空 content」。同一页给到 8192 就是正常的 11 条。**所以 `llm_max_tokens` 默认 8192**：它是上限不是目标，调大几乎不花钱，调小是实打实地毁数据。`LLMClient.complete` 现在会把 `finish_reason` 写进错误消息，一眼能分出「预算被思考吃完了」和「模型就是没说话」。
- **但固定预算挡不住尾部，所以被截断要重试**：思考量随正文密度走，主题 C（Rust 调度器）上 5/30 条来源的 `reasoning_content` 在 1.5~2.3 万字（约 1.3 万 token），8192 照样吃满。这类失败**可恢复**——同一份 prompt 换更大的预算就能过，直接放弃等于白扔读得下来的页面。所以 `complete` 把这种情况抛成 `LLMTruncatedError`（**不是**靠匹配错误字符串），`reader.py` 捕获后用 `llm_retry_max_tokens`（默认 32768，接口上限 65536）重试一次，再失败才记 0 条笔记。重试只在真被截断时发生，常态下零额外开销。**超时/网络这类普通 `LLMError` 不重试** —— 同一份 prompt 重发没有意义。
- **prompt 模板用 `string.Template`（`$var`）而非 `str.format`** —— JSON 示例里的大括号会把 `.format()` 炸掉。
- **反爬站点**（微信公众号 / 知乎 / 小红书等）直接进黑名单不抓，降级用搜索摘要，标 `content_origin="search_summary"`。

### API 层

`api.py` 拆成「`POST /api/research` 创建 + `GET .../events` 订阅」两步——**EventSource 只支持 GET，带不了 body**，所以无法用单个流式 POST 完成。`POST` 起一个 `asyncio.create_task` 就返回 `202`，研究在后台跑。

几个不写下来就会踩的点：

- **先订阅、再重放**。反过来的话这两步之间到达的事件会永久丢失。代价是重放与直播必然重叠，所以靠 `seq` 去重。`since` 取 `Last-Event-ID` 头和 `?since=` 里较大的那个。
- **`TRANSIENT_TYPES`（目前只有 `status`）在重放时跳过**。它是纯进度显示，重连时重放只会把进度条拽回去。
- **`error` 事件不一定终结 run**。单次搜索 429、单页抓取超时只发个 `fatal=False` 的事件继续走；只有 `fatal=True` 的才把状态置为 `failed`。别把"看到 error"当成"研究挂了"。
- **内存缓冲，不落盘**：`deque(maxlen=2000)` + `MAX_RUNS=50` 淘汰最早的已完成 run。进程重启即清空（本版明确不做持久化）。
- **写 `TestClient` 测试必须用 `with TestClient(app):`**。不这么写的话每次请求新开一个 portal，`create_task` 起的后台任务会随那次请求的循环一起被销毁，于是「创建后还能查到进度」这条最关键的契约根本测不出来。
