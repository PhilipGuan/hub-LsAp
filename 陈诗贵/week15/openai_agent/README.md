# Week15 作业 · 多进程情绪疏导系统（OpenAI Agents SDK 版）

同一业务流程的**第二套独立实现**：编排层从 LangGraph 换成 **OpenAI Agents SDK**（`openai-agents`），
其余四进程职责与 LangGraph 版一致，额外支持 **SSE 流式输出**。

两套可同时启动、互不干扰（端口不同段）。

---

## 1. 需求与目标

用户输入一段文字：

1. **进程1 · 情感识别**（:5012）：`SentimentAgent` 判断 `positive` / `negative`
2. **进程2 · 共情回复**（:5013）：`EncourageAgent`（鼓励）/ `ComfortAgent`（排解）
3. **后端进程**（:5010）：`TriageAgent` 用 SDK 原生 **handoff** 把任务分流给进程2的 Agent，
   并通过 `@function_tool` 调用进程1、进程2 的 HTTP 服务
4. **前端进程**（:5011）：聊天页，**流式**逐字显示回复 + 实时展示 agent 切换 / 工具调用轨迹

### 明确的非目标

| 不做 | 原因 |
| --- | --- |
| 多轮记忆 | 单轮已覆盖题目要求；两版保持一致 |
| 用户系统 / 持久化 | 本地作业演示 |
| 细粒度情绪分类 | 题目只要求正/负二分 |
| 与 LangGraph 版共用代码 | 用户要求「完全独立的第二套」，便于对比教学 |

---

## 2. 架构

```
┌──────────────────────┐  SSE + POST   ┌─────────────────────────────────┐
│ 前端进程 :5011        │ ────────────► │ 后端进程 :5010                   │
│ frontend_service     │               │ TriageAgent（Agents SDK）        │
│ 流式渲染 + 轨迹展示   │ ◄──────────── │  ├─ tool: analyze_sentiment      │
└──────────────────────┘  event-stream └──┼──────────────────────────────┘
                                         │ HTTP            │ handoff
                                   ┌─────▼──────┐   ┌──────▼──────────────┐
                                   │ 进程1 :5012 │   │ 进程2 :5013          │
                                   │ Sentiment   │   │ Encourage / Comfort │
                                   │ Agent       │   │ Agent               │
                                   └────────────┘   └─────────────────────┘
```

### 端口（与 LangGraph 版错开，可同时运行）

| 端口 | 进程 | LangGraph 版对应端口 |
| --- | --- | --- |
| 5011 | 前端 | 5001 |
| 5010 | 后端 | 5000 |
| 5012 | 进程1 情感识别 | 5002 |
| 5013 | 进程2 共情回复 | 5003 |

---

## 3. Agent 设计（Agents SDK）

### 3.1 进程1：SentimentAgent

```python
Agent(
    name="SentimentAgent",
    model=deepseek-flash,
    instructions="二分类…只输出 JSON {label, score, reason}",
)
```

- DeepSeek 兼容接口**不支持** `output_type`（探针实测 400：`response_format type is unavailable`），
  所以 Agent 输出 JSON **文本**，服务端用 `extract_json()` 解析并校验；解析失败重试一次，仍失败返回 500。
- 标签非法（如模型给了 neutral）按约定归 `negative`。

### 3.2 进程2：EncourageAgent / ComfortAgent

- 两个独立 Agent，各自固化语气要求（鼓励 / 排解），通过 `Runner.run` 跑；
- 对 `label=positive` 选 Encourage，`negative` 选 Comfort，接口与 LangGraph 版一致。

### 3.3 后端：TriageAgent + handoff

```python
encourage_agent = Agent(name="Encourage", tools=[generate_encouragement],
                        handoff_description="用户情绪积极时接手，生成鼓励回复。")
comfort_agent   = Agent(name="Comfort",   tools=[generate_comfort],
                        handoff_description="用户情绪消极时接手，生成共情排解回复。")
triage_agent    = Agent(
    name="Triage",
    instructions="必须先调用 analyze_sentiment；positive→handoff Encourage，negative→handoff Comfort；不要自己回答",
    tools=[analyze_sentiment],
    handoffs=[encourage_agent, comfort_agent],
)
result = await Runner.run_streamed(triage_agent, message)
```

实测 trace：`["analyze_sentiment", "handoff:Comfort", "generate_comfort"]`，`agents` 为 `["Triage", "Comfort"]`。

- `analyze_sentiment` 是 `@function_tool`，内部 `requests.post(:5012)`；
- `generate_encouragement` / `generate_comfort` 是 `@function_tool`，内部 `requests.post(:5013)`，
  label 由工具名固定，不交给模型判断；
- **兜底**：若模型没有 handoff（`result.last_agent` 仍是 Triage），或没走进程2 的工具，
  用工具结果里的 `label` 显式跑对应回复 Agent，保证一定有确定性分支（trace 中标注 `fallback`）。

### 3.4 SSE 事件协议

`POST /api/chat/stream`（`text/event-stream`）

```
event: agent    data: {"name": "Triage"}
event: tool     data: {"name": "analyze_sentiment"}
event: handoff  data: {"to": "Comfort"}
event: delta    data: {"text": "被裁员真的很难受"}
event: done     data: {"sentiment": {...}, "mode": "comfort", "reply": "...", "trace": [...], "elapsed": 3.2}
event: error    data: {"error": "情感识别服务不可用: ..."}
```

- 只转发 `response.output_text.delta`，**过滤** `response.reasoning_summary_text.delta`（模型思考文本不进气泡）；
- 前端用 `fetch` + `ReadableStream` 解析，逐字追加到当前 bot 气泡。

同时保留非流式 `POST /api/chat`（自检、降级用），返回结构同 LangGraph 版。

---

## 4. 接口契约

| 服务 | 接口 | 说明 |
| --- | --- | --- |
| 进程1 | `POST :5012/api/analyze` | `{text}` → `{label, score, reason, elapsed}`；空文本 400 |
| 进程2 | `POST :5013/api/reply` | `{text, label, reason}` → `{mode, reply, elapsed}`；非法 label 400 |
| 后端 | `POST :5010/api/chat` | 非流式，返回完整结果；下游故障 502 |
| 后端 | `POST :5010/api/chat/stream` | SSE 流式，见 3.4 |
| 前端 | `GET :5011/` | 聊天页 |
| 前端 | `POST :5011/api/chat/stream` | 转发后端的 SSE（原样透传） |
| 全部 | `GET /health` | 探活 |

---

## 5. 目录结构与文件职责

```
week15/openai_agent/       # 本项目根目录（与 langgraph 版完全独立）
├── README.md              # 本文档
├── requirements.txt       # flask、openai-agents、openai、python-dotenv、requests
├── Dockerfile             # 单容器镜像：python:3.12-slim + 四进程，仅发布 5011
├── .dockerignore          # 构建上下文排除项（.env 等密钥绝不入镜像）
├── common.py              # 端口常量、BIND_HOST、.env 加载、SDK 初始化（client/api/tracing）、JSON 提取
├── sentiment_service.py   # 进程1：SentimentAgent → :5012
├── reply_service.py       # 进程2：EncourageAgent / ComfortAgent → :5013
├── backend_service.py     # 后端：TriageAgent + handoff + 工具 + SSE 事件流 → :5010
├── frontend_service.py    # 前端：页面托管 + SSE 透传 → :5011
├── run_all.py             # 端口检查 → 依次启动 → /health 就绪 → Ctrl+C 回收
├── static/
│   ├── index.html         # 聊天页：流式气泡、识别标签、agent/工具轨迹
│   ├── style.css          # 样式（与 LangGraph 版同一套视觉）
│   └── app.js             # SSE 解析、逐字渲染、轨迹徽章、错误提示
└── tests/
    └── selfcheck.py       # 断言式自检（含 SSE 流完整性用例）
```

配置复用 `root/.env`（`DS_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL`）。
SDK 初始化统一做三件事：`set_default_openai_client(AsyncOpenAI(base_url=...))`、
`set_default_openai_api("chat_completions")`、`set_tracing_disabled(True)`（DeepSeek key 无法上报 OpenAI trace）。
监听地址由 `BIND_HOST` 控制：本地默认 `127.0.0.1`，容器内固定 `0.0.0.0`（Dockerfile 已设置）。

---

## 6. 本地启动

```powershell
cd 作业\week15\openai_agent
pip install -r requirements.txt
python run_all.py            # 四进程全部拉起
# 浏览器 http://127.0.0.1:5011
python tests\selfcheck.py    # 另开终端跑自检
```

LangGraph 版可同时运行，两者端口不冲突。Docker 方式见第 10 节。

---

## 7. 测试用例

### 7.1 进程1 · 情感识别

| 编号 | 场景 | 期望 |
| --- | --- | --- |
| S-01 | 「今天终于升职了，太开心了」 | `label=positive`，score 0~1 |
| S-02 | 「我被裁员了，真的很难受」 | `label=negative` |
| S-03 | 中性「今天星期一」 | `label=negative`（约定） |
| S-05 | 空文本 | 400 + error |
| S-06 | 2000 字超长文本 | 200，不崩溃 |
| S-07 | 无 API Key | 500 + 明确提示 |
| S-08 | 任意合法输入 | `label/score/reason` 齐全，score ∈ [0,1] |

### 7.2 进程2 · 共情回复

| 编号 | 场景 | 期望 |
| --- | --- | --- |
| R-01 | label=positive | `mode=encourage`，回复 ≥60 字 |
| R-02 | label=negative | `mode=comfort`，含共情与建议 |
| R-03 | 非法 label | 400 |
| R-04 | 空文本 | 400 |

### 7.3 后端（Agents SDK 编排）

| 编号 | 场景 | 期望 |
| --- | --- | --- |
| B-01 | 正向端到端 | `mode=encourage`，trace 含 `analyze_sentiment` 与 `handoff:Encourage` |
| B-02 | 负向端到端 | `mode=comfort`，trace 含 `handoff:Comfort` |
| B-03 | 情感服务宕机 | 502 + 明确 error |
| B-04 | 回复服务宕机 | 502 + 明确 error |
| B-05 | 模型未 handoff（边界） | 兜底走 label 对应分支，trace 含 `fallback` |
| B-06 | 空消息 | 400 |
| B-07 | 非 JSON 请求体 | 400 |
| B-08 | 并发 5 请求 | 全部 200，不串数据 |

### 7.4 SSE 流式

| 编号 | 场景 | 期望 |
| --- | --- | --- |
| T-01 | 正向请求 | 依次收到 `agent` → `tool` → `handoff` → 多个 `delta` → `done` |
| T-02 | delta 拼接 | 所有 `delta.text` 拼接后等于 `done.reply` |
| T-03 | 思考文本过滤 | 流中不出现 `reasoning` 类型事件 |
| T-04 | 下游故障 | 收到单个 `error` 事件后连接关闭，HTTP 200 不挂死 |
| T-05 | 空消息 | 400，不开流 |

### 7.5 前端

| 编号 | 场景 | 期望 |
| --- | --- | --- |
| F-01 | 页面可访问 | 200，含输入框与发送按钮 |
| F-02 | 流式渲染 | 回复逐字出现，完成后才显示标签与轨迹 |
| F-03 | 空输入 | 按钮禁用，不发请求 |
| F-04 | 请求中 | 按钮显示「识别中…」且不可重复提交 |
| F-05 | 后端不可用 | 红色错误气泡，不白屏 |

### 7.6 进程

| 编号 | 场景 | 期望 |
| --- | --- | --- |
| I-01 | 一键启动 | 5010~5013 全部 `/health` 通过 |
| I-02 | Ctrl+C | 四进程退出，端口释放 |
| I-03 | 与 LangGraph 版并行 | 两套同时运行互不影响 |

---

## 8. 验收标准

1. `python run_all.py` 拉起四进程，页面可交互且**逐字**输出。
2. S-01/S-02、R-01/R-02、B-01/B-02、T-01/T-02 通过（截图 + 终端输出存档）。
3. `tests/selfcheck.py` 全绿。
4. 停掉任一子服务，页面出现明确错误气泡，不假回复。
5. 与 LangGraph 版同时启动，端口无冲突。

---

## 9. 与 LangGraph 版对比

| 维度 | LangGraph 版 | OpenAI Agents SDK 版 |
| --- | --- | --- |
| 编排方式 | 显式 `StateGraph` 节点 + 条件边 | `Runner` + Agent `handoff`（模型驱动分流） |
| 分支确定性 | 代码里写死条件边，100% 确定 | 模型决定 handoff，带兜底重跑 |
| 状态管理 | `TypedDict` state 在节点间传递 | SDK 内部 `RunResult.new_items` |
| 流式 | 不支持 | 原生 `run_streamed` + SSE |
| 结构化输出 | 自己解析 JSON | 本可 `output_type`，DeepSeek 不支持 → 仍解析 JSON |
| 轨迹 | 手动 `trace` 列表 | `stream_events()` 自动给出 agent/tool/handoff 事件 |
| 进程架构 | 四进程（5000~5003） | 四进程（5010~5013），完全独立 |

---

## 10. Docker 部署

### 10.1 设计说明

| 决策 | 原因 |
| --- | --- |
| 单容器 + `run_all.py` 拉起四进程 | 符合「一个 Dockerfile 打包」的要求，命令最少 |
| 只发布 `5011` | 外部只需要访问前端；5010/5012/5013 仅在容器内网通信 |
| `.env` 不打进镜像 | 密钥不入镜像层，运行时用 `--env-file` 注入；`.dockerignore` 与显式 `COPY` 双保险 |
| 镜像内 `BIND_HOST=0.0.0.0` | 否则端口映射无效；本地开发仍默认 `127.0.0.1` |
| 非 root（uid 10001） | 降低容器逃逸风险 |
| `HEALTHCHECK` 探测前端 `/health` | 四进程就绪后容器才标记 healthy |

> 当前开发机未安装 Docker / WSL，因此镜像需在装有 Docker Desktop（或 Docker Engine）的机器上构建。

### 10.2 构建

在 `week15/openai_agent` 目录下执行：

```bash
docker build -t week15-openai-agent:1.0 .
```

Dockerfile 默认使用腾讯云 PyPI 镜像，pip 带 `--timeout 120 --retries 5 --prefer-binary`，
避免大包下载时出现 `ReadTimeoutError`。海外网络或想用官方 PyPI：

```bash
docker build \
  --build-arg PIP_INDEX_URL=https://pypi.org/simple \
  --build-arg PIP_TRUSTED_HOST=pypi.org \
  -t week15-openai-agent:1.0 .
```

`requirements.txt` 锁定为本地已验证的版本组合（flask 3.1.3 / openai 2.46.0 /
openai-agents 0.20.0），避免构建时拉到不同版本导致行为不一致。

### 10.3 运行

```bash
# Linux / macOS：.env 位于仓库根目录，向上三级
docker run -d --name emotion-agent \
  -p 5011:5011 \
  --env-file ../../../.env \
  week15-openai-agent:1.0
```

```powershell
# Windows PowerShell
docker run -d --name emotion-agent `
  -p 5011:5011 `
  --env-file ..\..\..\.env `
  week15-openai-agent:1.0
```

打开 `http://127.0.0.1:5011`。

也可以不用 `--env-file`，逐个传：`-e DS_API_KEY=... -e LLM_BASE_URL=... -e LLM_MODEL=...`。

### 10.4 验证与运维

```bash
docker ps                                   # STATUS 应显示 (healthy)，约 1 分钟后转为 healthy
docker logs -f emotion-agent                # 四个进程的启动日志 + 请求日志
docker inspect --format '{{.State.Health.Status}}' emotion-agent

# 容器内跑完整自检（19 条用例）
docker exec -it emotion-agent python tests/selfcheck.py

# 停掉/清理
docker stop emotion-agent && docker rm emotion-agent
```

### 10.5 常见问题

| 现象 | 原因与处理 |
| --- | --- |
| `docker build` 拉基础镜像很慢 | 给 Docker Desktop 配镜像加速器后重试 |
| pip 安装慢/超时 | 默认已用腾讯云源并带 `--timeout 120 --retries 5`；仍失败可按 10.2 换源重试 |
| 页面打不开 | `docker logs` 看是否 `未配置 DS_API_KEY`；确认 `--env-file` 路径正确 |
| 端口冲突 | 改映射：`-p 15011:5011`，容器内端口不变 |
| 容器启动后一直 unhealthy | 首次启动需等四个进程就绪，`start-period=60s`；仍失败看日志里的启动超时提示 |
| 想调试内部服务 | 临时映射全部端口：`-p 5010:5010 -p 5012:5012 -p 5013:5013`（仅调试用） |

### 10.6 镜像体积与优化（可选）

当前仅安装运行期依赖，`python:3.12-slim` 基础镜像约 130MB，依赖层约 200MB。
若需进一步压缩，可改用多阶段构建 + `pip install --prefix`，本次作业不做（收益有限、增加复杂度）。

