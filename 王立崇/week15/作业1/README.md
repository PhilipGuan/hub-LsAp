# 情绪陪伴四进程流水线：LangGraph / OpenAI Agents 双实现

同一套业务逻辑，用两个主流 Agent 框架各实现一遍：

| 进程 | 职责 | LangGraph 版 | OpenAI Agents 版 |
| --- | --- | --- | --- |
| 前端进程 | CLI 或 FastAPI + WebSocket，收集输入、展示回复 | `shared.runtime.frontend_main` / `shared.web_frontend.py` | 同左（共享实现） |
| 后端进程 | 接收前端输入，串联进程1、进程2 | `StateGraph` + `MemorySaver` | conductor Agent + handoffs |
| 进程1 | 情感识别（正向 / 负向 / 中性） | 本地模型 / OpenAI 客户端 / 词典兜底 | 本地模型 / `Agent(output_type=...)` / 词典兜底 |
| 进程2 | 正向 → 鼓励；负面 → 排解 | 按 style 调用同一进程 | 按 style 选择不同 Agent |

四个进程都是真正的 OS 进程（`multiprocessing`，Windows 下 `spawn` 启动），
彼此不共享内存，只通过 `multiprocessing.Queue` 传递 dataclass 消息。

## 架构

```
┌────────────┐   UserRequest    ┌─────────────┐  SentimentRequest  ┌────────────────┐
│  前端进程   │ ───────────────▶ │  后端进程    │ ─────────────────▶ │ 进程1 情感识别  │
│ CLI / Web  │ ◀─────────────── │ (串联/编排)  │ ◀───────────────── │ 模型/LLM/规则   │
└────────────┘   ReplyResult    └──────┬──────┘  SentimentResult   └────────────────┘
                                       │
                               SupportRequest
                                       ▼
                                ┌────────────────┐
                                │ 进程2 鼓励/排解 │
                                └────────────────┘
```

一次对话的完整链路：

1. 前端进程把 `UserRequest(rid, text, session_id)` 放进 `front_in` 队列；
2. 后端进程取出请求，调用自己的 orchestrator；
3. orchestrator 把 `SentimentRequest` 发给进程1，阻塞等待 `SentimentResult`；
4. 根据情感倾向确定 style（positive→encourage，negative→comfort，其他→neutral），
   把 `SupportRequest` 发给进程2，等待 `ReplyResult`；
5. 后端把回复放回 `front_out`，前端打印。

每个请求都带 `rid`（不会串消息）和 `session_id`（多轮记忆的 key）；
任意进程异常时统一返回 `ErrorResult`。

前端进程有两种形态，二选一：

- CLI：`python run_agents.py`；
- FastAPI + WebSocket：`python run_agents.py --web`，此时前端进程变成 HTTP 服务，
  多个客户端可以并发接入，WebSocket 以增量事件推送回复。

## 两套实现的差异

### LangGraph 版（`langgraph_app/`）

图定义在 [langgraph_app/backend.py](langgraph_app/backend.py)：

```
analyze ──positive──▶ encourage ──▶ remember ──▶ END
   │──negative──▶ comfort ──▶ remember ──▶ END
   └──neutral───▶ neutral ──▶ remember ──▶ END
```

- `analyze` 节点内部通过队列调用进程1；
- 三个回复节点内部通过队列调用进程2，只是传入的 `style` 不同；
- "正向就鼓励、负面就排解"直接体现为 `add_conditional_edges` 的分支；
- `remember` 节点把本轮问答追加进 `history`，配合 `MemorySaver` 实现多轮记忆。

### OpenAI Agents 版（`agents_app/`）

- 进程1：`Agent(name="sentiment_analyst", output_type=SentimentOutput)`，
  用 Pydantic 模型约束输出，SDK 会自动校验/重试；若模型服务不支持
  `json_schema`，会自动降级为"普通 Agent + JSON 文本解析"，再不行才退回词典规则；
- 进程2：`encourage` / `comfort` / `neutral` 三个不同指令的 Agent，按 style 选用；
- 后端：conductor Agent 先调用 `analyze_sentiment` 工具（进程1），再根据结果
  **handoff** 给三位专家 Agent 之一；专家调用 `generate_reply` 工具（进程2）产出回复。
  后端会校验两个进程都被调用过，若模型绕过它们自己编回复，会自动回退到直接串联。

## 快速开始

需要 Python 3.10+。

```bash
pip install -r requirements.txt
```

交互模式：

```bash
python run_langgraph.py      # LangGraph 版
python run_agents.py         # OpenAI Agents 版
```

非交互模式（便于验证 / 写脚本）：

```bash
python run_langgraph.py --text "今天升职了，太开心了！" --text "最近压力好大，天天失眠"
python run_agents.py    --text "今天升职了，太开心了！" --text "最近压力好大，天天失眠"
```

Web 模式（前端进程换成 FastAPI + WebSocket，浏览器打开 `http://127.0.0.1:8000`）：

```bash
python run_langgraph.py --web --port 8000
python run_agents.py --web --port 8001
```

### 离线体验（无需 API Key）

不配置任何密钥时：

- 进程1 使用 `shared/sentiment.py` 中的中文情绪词典；
- 进程2 使用与 style 对应的模板回复；
- Agents 版的后端跳过 conductor，直接按要求顺序串联两个进程。

因此 `pip install` 完成后即可直接运行，整条多进程链路与真实 LLM 模式完全一致。
日志形如：

```
[pipeline] 进程1 情感识别：positive (confidence=0.75, source=rule)
[pipeline] 进程2 回复风格：encourage (source=rule)
```

### 接入真实模型

复制 `.env.example` 为 `.env` 并填写。示例（DeepSeek）：

```dotenv
DEEPSEEK_API_KEY=sk-xxxx
```

或任意 OpenAI 兼容服务：

```dotenv
OPENAI_API_KEY=sk-xxxx
OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
OPENAI_MODEL=qwen-plus
```

模型调用失败（超时、限流、返回格式不对）时会自动退回离线模式，不会中断流水线。

## 扩展实现

### 1. 前端进程：FastAPI + WebSocket

实现位于 [shared/web_frontend.py](shared/web_frontend.py)，通过 `--web` 启用，
进程边界完全不变：

```
FastAPI 进程 ── front_in  ──▶ 后端进程 ──▶ 进程1 / 进程2
             ◀── front_out ──
```

`multiprocessing.Queue` 是阻塞 API，Web 进程用一个后台线程把结果搬进 asyncio 事件循环，
按 `rid` 找到每个请求自己的 Future，所以多个 HTTP / WebSocket 客户端可以并发使用。

接口：

| 接口 | 说明 |
| --- | --- |
| `GET /` | 内置聊天页面（原生 JS，无需构建） |
| `GET /healthz` | 健康检查 |
| `POST /chat` | `{"text": "...", "session_id": "..."}` → `{"rid", "reply"}` |
| `WS /ws/chat` | 发送 `{"text": "..."}`，接收 `ready / thinking / start / delta / done / error` 事件 |

WebSocket 连接可用 `?session_id=xxx` 指定会话；不传则每个连接自动生成一个。
POST 请求默认会话是 `http-default`，建议客户端显式传入 `session_id`：

```bash
curl -s http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"text": "今天升职了，太开心了！", "session_id": "demo"}'
```

流式说明：进程2 当前返回的是完整文本，Web 层按 `WS_CHUNK_SIZE`（默认 4 个字符）
分片下发，`WS_CHUNK_DELAY`（默认 0.02s）控制节奏。若要真正的 token 级流式，
需要让进程2 通过队列发送增量事件，协议层已经预留了 `rid`，改动集中在
`SupportRequest/ReplyResult` 和 `support_process`。

### 2. 进程1：替换为本地微调分类模型

实现位于 [shared/sentiment_model.py](shared/sentiment_model.py)。
协议不变：仍然消费 `SentimentRequest`、产出 `SentimentResult`，只是 `source` 变成 `"model"`。

```bash
pip install -r requirements.model.txt
```

```dotenv
SENTIMENT_MODEL_PATH=/models/my-finetuned-sentiment
SENTIMENT_DEVICE=-1
SENTIMENT_LABEL_MAP={"LABEL_0": "negative", "LABEL_1": "positive"}
```

启用后进程1 的优先级为：**本地模型 → LLM → 词典规则**。
模型输出标签支持 `positive` / `LABEL_0` / `1 star` / `正向` 等常见写法自动归一化；
`LABEL_0/1/2` 这类无语义标签默认按"0 负向、末位正向、中间中性"处理，
如果和你的微调模型不一致，用 `SENTIMENT_LABEL_MAP` 覆盖即可。

Docker 镜像默认不装 transformers/torch，需要时：

```bash
docker build --build-arg WITH_LOCAL_MODEL=1 -t week15-agents:model .

docker run --rm -p 8000:8000 --env-file .env \
  -v /host/models/sentiment:/models/sentiment:ro \
  -e SENTIMENT_MODEL_PATH=/models/sentiment \
  week15-agents:model --web --host 0.0.0.0
```

### 3. LangGraph：MemorySaver 多轮记忆

[langgraph_app/backend.py](langgraph_app/backend.py) 用 `MemorySaver` 做检查点：

- `thread_id` = `session_id`，同一会话的多轮输入共享一份 state；
- `history` 使用 reducer 累积，`remember` 节点在每轮结束时写入 `{"user", "assistant"}`；
- 回复节点最多把最近 8 轮历史放进 `SupportRequest.history`，进程2 取其中最近 4 轮组织提示词；
- 每个会话最多保留 20 轮，避免无限增长；记忆只在后端进程内存中，重启即清空。

CLI 会话固定用 `cli`；Web 模式下每个 WebSocket 连接一个会话，
也可以通过 `?session_id=` 或消息体里的 `session_id` 续接同一段对话。

### 4. OpenAI Agents：conductor + handoffs

[agents_app/backend.py](agents_app/backend.py) 改成"分诊 + 交接"结构：

```
conductor
   ├─ tool analyze_sentiment ──▶ 进程1
   └─ handoff
        ├─ encourager ── tool generate_reply ──▶ 进程2（encourage）
        ├─ comforter  ── tool generate_reply ──▶ 进程2（comfort）
        └─ neutral_responder ── tool generate_reply ──▶ 进程2（neutral）
```

conductor 只负责调用进程1 并决定交给哪位专家；最终回复由专家调用进程2 产生。
后端会统计 `analyze_sentiment` 和 `generate_reply` 的调用次数，并直接采用
进程2 的原始输出作为最终回复，避免模型自行改写；任何一步没走通都会回退到
`run_direct`（后端直接串联进程1 → 进程2），所以离线模式依然可用。

注意：Agents 版目前只做单轮 handoff，不维护跨轮记忆；`session_id` 只用于日志。
跨轮记忆能力加在 LangGraph 版上（见上一节）。

## Docker 打包（openai-agents 版本）

镜像只包含作业1（openai-agents 版）所需的代码和依赖，LangGraph 版本不会打进镜像。

```bash
# 在 d:/week15 下构建
docker build -t week15-agents:latest .

# 交互模式（必须带 -it，否则拿不到 stdin）
docker run --rm -it --env-file .env week15-agents:latest

# 非交互模式
docker run --rm --env-file .env week15-agents:latest --text "今天升职了，太开心了！"

# Web 模式：前端进程变成 FastAPI + WebSocket 服务，浏览器访问 http://localhost:8000
docker run --rm -p 8000:8000 --env-file .env \
  week15-agents:latest --web --host 0.0.0.0
```

没有 `.env` 也能跑（会自动进入词典 + 模板的离线模式）：

```bash
docker run --rm -it week15-agents:latest
```

国内构建加速：

```bash
docker build \
  --build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
  -t week15-agents:latest .
```

### 推送到阿里云容器镜像服务（ACR）

1. 控制台开通「容器镜像服务」个人版（免费），创建命名空间（示例 `week15`）和镜像仓库（示例 `emotion-agents`）。
2. 登录仓库（密码在控制台「访问凭证」中设置）：

   ```bash
   docker login --username=<阿里云账号> registry.cn-hangzhou.aliyuncs.com
   ```

3. 打标签并推送（区域按实际实例替换，如 `registry.cn-shanghai.aliyuncs.com`）：

   ```bash
   docker tag week15-agents:latest \
     registry.cn-hangzhou.aliyuncs.com/week15/emotion-agents:1.0.0

   docker push \
     registry.cn-hangzhou.aliyuncs.com/week15/emotion-agents:1.0.0
   ```

4. 在任意装好 Docker 的机器上拉取运行：

   ```bash
   docker pull registry.cn-hangzhou.aliyuncs.com/week15/emotion-agents:1.0.0

   docker run --rm -it --env-file .env \
     registry.cn-hangzhou.aliyuncs.com/week15/emotion-agents:1.0.0
   ```

补充说明：

- Apple Silicon 上构建 x86 镜像：`docker buildx build --platform linux/amd64 -t <镜像名> .`，推送时加 `--push`；
- 也可以走 ACR「云端构建」：镜像仓库 → 创建仓库 → 代码源选 GitHub / 云效 Codeup → Dockerfile 路径填 `/Dockerfile`，之后每次推代码自动构建；
- `.env` 已被 [.dockerignore](.dockerignore) 排除，密钥只通过 `--env-file` 或 `-e OPENAI_API_KEY=...` 在运行时注入；
- 阿里云 ECS 上运行可把 `--env-file` 换成企业级 KMS / 环境变量配置，不要提交到代码仓库。

## 目录结构

```
week15/
├─ Dockerfile                  # 作业1（openai-agents 版）镜像
├─ .dockerignore
├─ requirements.agents.txt     # 镜像内依赖（不含 langgraph）
├─ requirements.model.txt      # 可选：本地微调模型依赖
├─ run_langgraph.py            # LangGraph 版入口
├─ run_agents.py               # OpenAI Agents 版入口
├─ requirements.txt
├─ .env.example
├─ shared/                     # 两套实现共享
│  ├─ protocol.py              # 进程间消息（dataclass）
│  ├─ runtime.py               # 进程骨架：serve / call_stage / CLI 前端 / 后端循环
│  ├─ web_frontend.py          # FastAPI + WebSocket 前端进程
│  ├─ config.py                # 环境变量 / .env 配置
│  ├─ llm.py                   # OpenAI 兼容接口封装
│  ├─ sentiment.py             # 离线兜底：情绪词典 + 模板回复
│  ├─ sentiment_model.py       # 可选：本地微调情感分类模型
│  └─ history.py               # 多轮历史格式化
├─ langgraph_app/
│  ├─ processes.py             # 进程1、进程2 的实现
│  ├─ backend.py               # StateGraph + MemorySaver 编排
│  └─ main.py                  # 组装四个进程（CLI / Web）
└─ agents_app/
   ├─ runtime_setup.py         # Agents SDK 全局配置（base_url / tracing）
   ├─ sentiment_process.py     # 进程1：Agent + output_type
   ├─ support_process.py       # 进程2：按 style 选择 Agent
   ├─ backend.py               # conductor + handoffs 编排
   └─ main.py                  # 组装四个进程（CLI / Web）
```

## 继续可扩展的方向

- 真正的 token 级流式：让进程2 通过队列发送 `delta` 事件，Web 层原样转发；
- 持久化记忆：把 `MemorySaver` 换成 `SqliteSaver` / `PostgresSaver`，重启不丢会话；
- 认证与限流：Web 前端目前是演示用、无鉴权，公网部署前应加 Token / 会话校验；
- 进程间协议已经是 dataclass，迁移到 Redis / RabbitMQ 只改 `call_stage` 与 `serve`；
- 在 ACR 中配置代码源自动构建，把镜像发布接进 CI。
