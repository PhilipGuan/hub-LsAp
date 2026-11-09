# CLAUDE.md

> 本文件是 Claude Code 在本项目中的**长期记忆**：项目是什么、为什么这样设计、改动代码前必须遵守什么。
> **动手改代码前，请先读完「关键实现决策」和「编码约定」两节。** 这两节里的每一条都是与用户逐条敲定的结论，不是默认值。

---

## 项目概览

**深度研究助手**：输入一个研究主题（自然语言），由多个 Agent 协作完成「规划 → 多轮检索 → 分节总结 → 充分性判断 → 迭代补检 → 综合成文」，产出一份带来源引用与置信度说明的结构化研究报告。

与"一次性问答"的本质区别：**先拆题、再检索、检索后自评、不充分则补检**——是一个带反馈回路的迭代过程。

**四项交付物**：① 结构化研究报告 ② 来源列表（可追溯）③ 研究过程记录 ④ 置信度说明。

**当前状态：全部落地，无待实现项。** 已完成 `app/config.py`、`app/main.py`（三个 API 路由 + `/health`）、`app/models.py`、`app/render.py`、`app/agents/base.py`（`BaseAgent`）、`plan.py` / `summary.py` / `judge.py` / `report.py` 四个 Agent 及 `app/prompts/` 下对应模板，均真实验证通过；`app/tools/search.py`（博查实跑通）、`app/tools/confidence.py`、`app/orchestrator.py`（真实跑过一次完整研究：3 轮 / 10 分节 / 66 条检索结果 / 35 秒，产物落盘并逐项核对过编号与章节；`stream()` 事件出口也已实跑验证——事件在研究过程中陆续推出，不是跑完一次性给）也已落地。API 层同样实跑验证过：`POST` 立即返回 `task_id`、`GET` 返回第 7 节产物结构、SSE 在真实 uvicorn 下**逐条推**（curl 实测 5.1s→18.0s 陆续到达，`done` 收尾），`?after=<seq>` 断线续传与未知任务 404 均验过。**验收项 9 的「人为注入」测试已落进 `orchestrator.py` 的自测**（`_check_injected()`：把搜索的 `_fetch` 与 Agent 基类的 `_chat` 换成故障版，断言任务仍 `succeeded`、六章节齐、置信度 0.00、降级原因全部留痕），不再依赖"碰巧遇到"。**实现时以 `docs/需求文档.md` 为唯一需求依据**，本文件是它的浓缩版 + 实现约定，若两者冲突，以本文件的「关键实现决策」为准。

**技术栈**：Python + FastAPI；qwen-flash 作为统一 LLM（OpenAI 兼容协议）；博查 Web Search 作为唯一检索源。

**Demo 边界（有意为之，不要"顺手补上"）**：单进程、内存态、无鉴权、无数据库、不面向多实例部署。

---

## 核心架构

```
客户端 ──HTTP/SSE──> FastAPI 服务层 ──> ResearchOrchestrator
                                              │  （唯一状态持有者：轮次 / 并发 / 降级）
                        ┌──────────┬──────────┼──────────┬──────────┐
                    PlanAgent  SummaryAgent JudgeAgent ReportAgent   search tool
                        └──────────┴──────────┴──────────┘           （非 Agent）
                                  继承 BaseAgent                        │
                                        │                          api.bocha.cn
                                    qwen-flash
```

### 职责边界

| 组件 | 类型 | 职责 |
|---|---|---|
| `BaseAgent` | 基类 | LLM 调用、空输出自动重试、JSON 解析兜底、提示词模板渲染 |
| `PlanAgent` | Agent | 主题 → 2~5 个可直接检索的子问题 |
| `SummaryAgent` | Agent | 单个子问题 + 其全部来源 → `{section_text, refs}`，**即报告的分节正文** |
| `JudgeAgent` | Agent | 主题 + 全部分节正文 → 充分性判定 + 2~3 个新子问题 |
| `ReportAgent` | Agent | 生成摘要 / 关键结论 / 遗留问题 / 置信度说明 |
| `search` | **tool** | 博查搜索封装，**不继承 BaseAgent** |
| `ResearchOrchestrator` | 普通类 | 持有任务状态、控制轮次、并发调度、异常降级 |

### 一次研究的完整数据流

1. `PlanAgent` 拆解主题 → 子问题列表 `Q`
2. 第 `r` 轮（`r` = 1..3）：
   1. 对 `Q` 中每个子问题调用 `search` tool（第 1 轮 `count=10`，补检轮 `count=5`），并发上限 2
   2. 每个子问题调用 `SummaryAgent` → `{section_text, refs}`，成为报告的一节
   3. `r < 3` 时调用 `JudgeAgent`；`sufficient=true` 则跳出循环，否则以 `new_sub_questions` 作为下一轮的 `Q`
   4. `r == 3` 时**仍然调用** `JudgeAgent`，记录结论但不再循环
3. `ReportAgent` 生成报告其余章节
4. **程序**按公式计算置信度并渲染 Markdown

### 目录结构（✅ 已创建 ／ ⬜ 规划中）

```
app/
  __init__.py        # ✅ 使 app 成为 package（`python -m app.main` 依赖它）
  config.py          # ✅ 环境变量集中读取
  main.py            # ✅ FastAPI 入口：/health + 研究三接口（提交 / 查询 / SSE）
                     #    服务层不含业务，只把请求转给进程级单例 `orchestrator`；`_spawn()` 留引用防任务被 GC
  orchestrator.py    # ✅ ResearchOrchestrator（唯一状态持有者：轮次 / 并发 / 降级 / 落盘 / 事件流）
                     #    事件出口 `stream()` 供 SSE 用：先补发已发生的，再 await wakeup 等新的，收到 done 收尾
  agents/
    __init__.py      # ✅
    base.py          # ✅ BaseAgent
    plan.py          # ✅ PlanAgent
    summary.py       # ✅ SummaryAgent
    judge.py         # ✅ JudgeAgent
    report.py        # ✅ ReportAgent
  tools/             # ✅ 「不吃 LLM 的东西」都住这里，与 Agent 层分开
    __init__.py      # ✅
    search.py        # ✅ 博查搜索封装（tool，非 Agent）
    confidence.py    # ✅ 置信度公式（纯函数，非 Agent）
  prompts/           # ✅ plan_* / summary_* / judge_* / report_* 模板齐备
  models.py          # ✅ 全部数据结构：Source / Section / SummaryResult / JudgeResult / ReportResult / Report / Confidence / StepRecord / SearchRecord / JudgeRecord / ResearchProcess / TaskState（含 `to_artifact()`）
  render.py          # ✅ 报告渲染：Markdown + 报告视图（`report` 子对象），纯函数
output/              # ✅ 任务结果落盘：{task_id}.json（契约产物）+ {task_id}.md（报告正文，直接打开看）
                     #    失败/取消的任务也落 JSON；没渲染出报告时就只落 JSON，不造空 .md
run.sh               # ✅ 启服务脚本：cd 到项目根 → 配置自检 → exec uvicorn（端口可传参）
docs/需求文档.md      # ✅ 需求与技术方案（已定稿）
requirements.txt     # ✅ 依赖声明
.env                 # ✅ 本地密钥，已被 .gitignore 忽略
```

---

## 常用命令

> 标 ✅ 的现在就能跑；标 ⬜ 的是为实现阶段预设的命令契约，实现时请让代码与这些命令保持一致，不要另立一套。

```bash
# 安装依赖 ✅
pip install -r requirements.txt

# 启动服务 ✅（推荐；自带配置自检，端口可换，浏览器访问 http://127.0.0.1:8000/health）
bash run.sh          # 等价于 bash run.sh 8000，需要在 Git Bash 中运行
# 或直接起 uvicorn（跳过配置自检），带热重载：
uvicorn app.main:app --reload --port 8000
# 注意：`python -m app.main` 不是启动服务，是跑自测（见下）——所有模块都遵守同一条约定

# 自测（约定见「编码约定 · 自测」）✅
python -m app.config
python -m app.models
python -m app.agents.base
python -m app.agents.plan      # 会真实调用一次 LLM
python -m app.agents.summary   # 同上
python -m app.agents.judge     # 同上
python -m app.agents.report    # 同上
python -m app.render           # 纯本地，会打印一份完整报告示例并写 output/render_selftest.md
python -m app.tools.search     # 会真实调用一次博查
python -m app.tools.confidence # 纯本地
python -m app.orchestrator     # 先跑无网络自检（注入降级 / 事件流 / 编号与落盘），再真实跑一次完整研究
python -m app.main             # TestClient 真实走一遍三个接口（约 40 秒）：POST → SSE 全过程 → GET

# 提交一次研究 ✅（立即返回 task_id，研究在后台跑）
curl -X POST http://localhost:8000/api/v1/research \
  -H "Content-Type: application/json" \
  -d '{"topic":"年轻人爱熬夜的原因"}'

# 查询任务状态与结果 ✅
curl http://localhost:8000/api/v1/research/{task_id}

# SSE 观察研究过程 ✅（已结束的任务会先补发全过程再收尾）
curl -N http://localhost:8000/api/v1/research/{task_id}/stream
```

**环境变量**（全部走 `.env`，不得硬编码；业务代码**只能从 `app/config.py` 读**）：

```
LLM_BASE_URL=https://ws-09qg8sou349yp1mg.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
LLM_MODEL=qwen-flash
LLM_API_KEY=<自行配置>
BOCHA_API_KEY=<自行配置>
```

> 🔐 `.env` 已被 `.gitignore` 忽略，密钥只在那里出现。**代码、测试、文档中一律不得再出现明文密钥**（`README.md` 里目前仍残留一个明文博查 key，属于待清理项）。

---

## 关键实现决策（改动前先读）

以下每一条都是与用户逐条确认过的结论。**改动其中任何一条之前，先确认用户是否真的要推翻它**——它们看起来"可以优化"，但都是权衡后的选择。

1. **检索实现为 tool，不是 Agent。** 博查搜索是对外部 HTTP 接口的封装、不含 LLM 推理，因此不继承 `BaseAgent`。`BaseAgent` 的统一埋点、重试、模板渲染对它没有意义。**不要"为了架构统一"把它改成 Agent。**

2. **总轮次上限 3 轮（首轮 + 最多 2 次补检），且不设 LLM 调用熔断。** 轮次是唯一的成本边界。曾有"25 次调用熔断"的设计，被用户明确去掉。**不要在实现时自作主张加回熔断。**

3. **第 3 轮结束后仍然调用 `JudgeAgent`，但忽略其"不充分"的判定。** 这是刻意的：跑满 3 轮仍不充分，这个结论本身是报告可信度的诚实体现，要写进 `process.judge_history` 并被「置信度说明」引用。**不要为了省一次调用而跳过它。**

4. **`JudgeAgent` 产出的是「子问题」，不是关键词。** 它输出的是 2~3 个聚焦缺失子角度的**新子问题**，这些子问题走"从搜索开始的完整步骤"并作为**新增分节**进入报告——因此报告结构会随轮次增长（上界 = 首轮 `MAX_SUB_QUESTIONS=5` + 补检 `MAX_NEW_SUB_QUESTIONS=3` × 2 = **11 个分节**，2026-09-10 实测跑出过 11 个；`docs/需求文档.md` 6.1 节写的 `4 + 2×3 = 10` 是按首轮 4 条算的旧口径），这是接受的已知特性。

5. **新子问题去重按「语义」由 LLM 判断，不做程序侧强校验。** 用户明确选择把去重交给 `JudgeAgent` 的提示词（要求其按语义而非字面判断），**不要自作主张加一套字符串去重逻辑**。这条已作为「已知局限」写入需求文档第 8 节，与置信度公式里的"域名多样性 / 交叉印证"项互相呼应（用以抵抗重复来源造成的虚高）。**注意：这一条与 `PlanAgent` 的程序侧去重不矛盾**——见第 6 条。

6. **`PlanAgent` 的产出去重由程序负责。** 与第 5 条相反：规划阶段要做规范化（去空白 / 全半角统一 / 转小写）去重、包含关系合并、超 5 截断、少于 2 个则回退为"原主题作为唯一子问题"。理由是规划是单次调用、规则化便宜且可验收；而补检轮的去重需要语义理解，规则做不到。

7. **`SummaryAgent` 输出 `{section_text, refs}`，段落内不含任何行内 `[n]`。** 报告的分节正文不做行内引用标注；节末的「本节来源：[n][m]」由**程序**用 `refs` 拼接。越界编号由程序剔除、去重、按全局来源表重编号。

8. **`ReportAgent` 不重写分节正文。** 它的输入只有各分节正文与 refs，产出仅限摘要、关键结论、遗留问题、置信度说明四块。分节正文**原样透传**。

9. **上下文按子问题分批，每次只喂「一个子问题 + 其全部来源」，每条 `summary` 截断 800 字。** 综合阶段只喂各分节正文，**绝不把原始摘要灌进综合调用**。

10. **置信度数值由程序算，`ReportAgent` 只负责用自然语言解释成因。** 公式（`app/tools/confidence.py`）：
    ```
    基线 0.30
    + 来源数量   min(来源数, 3) × 0.20            → 最多 +0.60
    封顶 0.90，四舍五入到 0.05
    分级：≥0.75 高 ｜ 0.50 ~ 0.75 中 ｜ <0.50 低
    无来源的分节记 0.00（不给基线分）
    ```
    整体置信度 = 各分节**按来源数量加权**平均；权重为 0 的分节（无来源）自然不参与整体分。
    **当前口径只看「真实来源数量」**——原设计里的域名多样性 / 权威来源 / 交叉印证三项，用户于 2026-09-10 明确「目前只需要按照真实来源数量判断」，故暂不参与；哪天要加回来，改的是 `_score()` 一处 + `config.py` 的 `CONFIDENCE_*` 常量。**所有阈值写成可配置常量，不要散落硬编码。**

11. **任一环节失败都不允许整个任务失败，必须降级返回部分结果。** 具体映射见需求文档第 6.3 节。最坏情况（未捕获异常）也要返回**已完成的部分结果 + 错误原因**，不能只回一个错误码。

12. **来源列表只列「被引用过」的来源**，编号与报告里的 `[n]` 严格一一对应；未被引用的检索结果只留在 `process` 里，不进报告。

13. **报告正文全中文；来源标题与 URL 保留原文不翻译。** 翻译标题会让点进去对不上，破坏可追溯性。

14. **报告的 `[n]` 用连续编号（1..N），重编号只由 `app/render.py` 做一次。** `Source.id` 的全局编号（跨轮次、跨子问题唯一，`process` 里保留）是检索侧的内部口径；报告视图按「被引用到的全局编号升序」重排成 `1..N`，参考来源列表因此不留空档。**结论的 `[n]`、分节的「本节来源」行、参考来源列表三处必须同号**——分散编号必然串号，而串号就是验收项 3 挂掉。
    > 全局编号**由 `ResearchOrchestrator` 分配**（`app/orchestrator.py` 的 `_register()`）：`search` tool 交回来的记录编号一律是 0，因为"已经发过哪些号"只有状态持有者知道。同一 URL 跨轮次、跨子问题复用同一个编号，否则同一篇文章会占两个号、并在「来源数量」里被重复计算。

---

## 编码约定（改动/新增代码前先读）

### Agent 层

- **`BaseAgent` 是模板方法模式**：子类**只实现 `_execute()`**。**严禁**在子类里重复实现 LLM 调用、空输出重试、JSON 解析兜底或模板渲染——这些是基类的职责，一旦重复实现，四个 Agent 的行为就会漂移。
- **产出收口也在基类**：`strip_inline_refs()`（正文里的行内 `[n]` 一律删掉）与 `valid_refs()`（越界编号剔除 + 去重 + 升序）是 `app/agents/base.py` 的模块级函数，`summary.py` / `report.py` 直接 import。**不许各写一份**——报告里「行内 `[n]` 只能由程序按 refs 生成」「越界编号必须消失」这两条不变量，一漂移就是验收项 3、4 挂掉。
- **新增 Agent = 新增 `BaseAgent` 子类 + 在 `ResearchOrchestrator` 里显式接线。** 不搞注册表、不搞装饰器自动发现、不搞 Agent 互相调用。流程是固定的，编排权只在 `ResearchOrchestrator` 手里。
- **`ResearchOrchestrator` 是唯一的状态持有者。** Agent 必须无状态，不得持有任务状态或跨调用缓存。
- **所有 LLM 调用只走 `BaseAgent.call_llm()`**，业务代码中不得直接实例化 LLM 客户端。

### 提示词

- **提示词一律放 `app/prompts/` 下的独立模板文件**，通过 `BaseAgent.render()` 注入变量。**不要把多行提示词硬编码在 Python 字符串里**——这是本项目提示词迭代最频繁的地方。
- 规划模板的硬性要求必须保留：输出 2~5 个"可直接投入搜索框的名词短语或疑问句"，禁止"背景/意义/概述"类无法检索的元问题，以 JSON 数组返回。**并明确要求"整个输出是一个数组"**——qwen-flash 实测会把每个子问题各包一个方括号、一行一个（`["A"]\n["B"]`），2026-09-10 撞到 6 次里挂 4 次，后果是整个研究退化成单分节。`BaseAgent.parse_json()` 对这种写法有兜底（`_merge_json_values()`，逐个 `raw_decode` 再拼平），但提示词那一句才是第一道闸。
- 判定模板必须写明按**语义**判断子问题是否重复。

### 异步与并发

- 对外接口与 I/O 全异步（`async def`）。
- 并发一律用 `asyncio.Semaphore` 控制：**同时最多 2 个研究任务**；单个任务内的检索/总结批次并发上限同为 2。这个数字是为防 LLM 限流而定的，调大前先确认配额。

### 错误处理

- **不吞异常**。允许降级的环节，降级必须在 `process` 里留痕（哪个 Agent、什么错、降级成了什么）。
- `BaseAgent` 负责空输出重试（最多 2 次，即共 3 次尝试）与指数退避（1s、3s）；业务层不要再套一层重试，否则重试次数会指数放大。

### 配置与常量

- 所有密钥与端点走环境变量 / `.env`，**不得硬编码**。
- 置信度各项权重、`count` 取值（10 / 5）、截断长度（800 字）、轮次上限（3）、并发数（2）、子问题条数上下限（`MAX_SUB_QUESTIONS=5` / `MIN_SUB_QUESTIONS=2`）等**魔法数字一律定义在 `app/config.py`**，业务模块 `from app.config import ...` 取用——不要散落在函数体内，也不要各自定义在模块里。

### 可观测性

- `process.steps` / `process.search_queries` / `process.judge_history` 的埋点由 `BaseAgent` 与编排器**统一产出**，业务代码里不要散打点。

### 自测（本项目硬性约定）

**每个 `.py` 文件末尾都必须有一段自测代码**，用来验证本文件自身的逻辑。形式固定、代码简单明了：

```python
if __name__ == "__main__":
    # 测试 demo：真实调用 LLM（需要 .env 里的 LLM_API_KEY 与网络）
    import asyncio
    import logging

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s | %(message)s")

    async def _demo() -> None:
        result = await PlanAgent().run(topic="2026 年主流 Agent 框架对比")
        print("拆解子问题:", result.value, "| step:", result.step)

    asyncio.run(_demo())
```

- **直接跑真实调用，`print` 出真实结果，肉眼对**——不要为了「像测试」去造假数据和写断言。只有无网络的纯本地逻辑（`config` / `models` / 置信度公式 / `parse_json` 这类）才造假数据打印自检结果
- Agent 的 demo 一律走 `run()`，顺便把 `step` 一起打出来（重试、耗时、错误都在这条埋点里）
- **不引入 pytest、fixtures、mock 框架**——本项目没有测试目录，自测就住在本文件里
- 运行方式：`python -m app.<模块名>`（不要用 `python app/xxx.py`，那样 `from app.config import ...` 会找不到包）
- Windows 终端跑中文输出前加一句 `sys.stdout.reconfigure(encoding="utf-8")`，否则 cp936 控制台乱码
- **`TestClient` 必须用 `with TestClient(app) as client:`**（`app/main.py` 自测）：不给请求单开一个事件循环，退出即销毁；`POST` 里 `create_task` 出去的后台研究是挂在那个循环上的，不用 `with` 的话请求一返回研究就被取消——症状是 SSE 的 `done` 事件里 `status` 停在 `running`。另外它会把 SSE 的响应体缓冲到结束才吐出来，所以**「事件是逐条推的」这件事只能对着真实 uvicorn 用 curl 验**，TestClient 只够验路由与契约。


### 命名与风格

- Agent 类名统一 `XxxAgent`，模块名 snake_case（`plan.py` 而非 `plan_agent.py` 之外的变体）。
- 数据结构（`TaskState` / `Source` / `Report` / `Section`）集中在 `models.py`。**Pydantic 只用在「LLM 输出要过闸」的结构上**（`SummaryResult` / `JudgeResult` / `ReportAgent` 的四块），且校验统一走 `BaseAgent.parse_model()`——它把 `pydantic.ValidationError` 折成 `LLMJsonError`，保证编排器的降级表始终只认 `LLMCallError` / `LLMJsonError` 两种错。**程序内部状态用 dataclass**（没有不可信输入要校验）；`PlanAgent` 的产物就是 `list[str]`，不包 Pydantic（`_as_list()` 已收口）。
- `models.py` 与 `BaseAgent.parse_model()` **等写 `SummaryAgent` 时一起落地**，不提前搭空骨架。
- 保持与既有代码一致的注释密度；中文注释写"为什么"，不写"是什么"。

---

## 参考代码

**只有骨架 + `BaseAgent`，无业务代码。** 作为范式参考的现成文件：

- `app/agents/base.py` —— **新增 Agent 的唯一范式参考**：模板方法（子类只实现 `_execute()`）、`call_llm()` 的空输出重试与退避、`render()` 模板注入、`parse_json()` 兜底、`parse_model()` 过闸、`run()` 的埋点与异常留痕，以及自测块模板。
- `app/agents/plan.py` / `app/agents/summary.py` —— 两个已落地的 Agent：前者示范「LLM 输出 + 程序侧规则收口」，后者示范「Pydantic 过闸 + 出处编号收敛」。
- `app/models.py` —— LLM 输出结构（Pydantic）与内部产物（dataclass）的分界示例。
- `app/config.py` —— 读环境变量的标准写法 + 自测块模板。
- `app/main.py` —— FastAPI 入口 + 路由自测块模板（`TestClient` 用法）。

四个业务 Agent 直接照 `base.py` 的 `_execute()` 签名与自测块来写，不要另立范式。

- `docs/需求文档.md` —— **唯一需求依据**。含完整架构图、报告模板、置信度公式、降级路径表、API 契约、产物 JSON 结构、10 项量化验收清单。
- `README.md` —— 作业原始需求（业务背景、四项交付物、博查 API 调用示例）。
- 博查 Web Search API：`https://api.bocha.cn/v1/web-search`，文档见 README 内链接。请求体关键字段：`query`、`summary: true`（**本项目直接以返回的 `summary` 作为"读到的内容"，不抓取网页正文**）、`count`。

> 当我明确说已经实现和验证成功时，才能当作实现推进了，请回到本文件，同步更新「当前状态」与「目录结构」的 ✅/⬜ 标记。
