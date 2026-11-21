# 情感支持对话系统

基于 **OpenAI Agents SDK** 构建的多进程情感识别与支持性对话系统。系统接收用户输入，先由情感识别 Agent 判定情绪正负及置信度，再由鼓励/排解 Agent 生成有温度、语气连贯的回复；低置信度时自动进入澄清追问流程；回复以 **SSE 流式** 推送至 Web 前端。

---

## 目录

- [架构概览](#架构概览)
- [核心特性](#核心特性)
- [技术栈](#技术栈)
- [项目结构](#项目结构)
- [消息契约](#消息契约)
- [环境准备](#环境准备)
- [配置项](#配置项)
- [启动方式](#启动方式)
- [API 说明](#api-说明)
- [澄清回环机制](#澄清回环机制)
- [流式输出机制](#流式输出机制)
- [会话历史管理](#会话历史管理)
- [可靠性设计](#可靠性设计)
- [可观测性](#可观测性)
- [常见问题](#常见问题)

---

## 架构概览

系统由 **三个独立部署的操作系统进程** 构成，通过 **Kafka** 进行可靠通信，通过 **Redis** 共享会话历史与 token 流旁路。

```
┌──────────────┐
│   Web 前端   │  EventSource (SSE)
└──────┬───────┘
       │ POST /chat/trigger  +  GET /chat/stream/{request_id}
       ▼
┌──────────────────────────────────────────────┐
│  FastAPI 后端（单 worker）                    │
│  - 接收 HTTP 请求，生成 request_id            │
│  - 发送 chat.request 到 Kafka                 │
│  - 全局 psubscribe stream:*，路由 token 到队列│
│  - SSE 推送 token 到前端                      │
└──────┬───────────────────────────────────────┘
       │ produce: chat.request
       ▼
┌──────────────────────────────────────────────┐
│  进程 1：情感识别                              │
│  - Kafka consumer (group: sentiment-svc)      │
│  - Runner.run(SentimentAgent, message)        │
│  - 输出 sentiment + confidence（结构化）       │
│  - produce: sentiment.result                  │
└──────┬───────────────────────────────────────┘
       │ produce: sentiment.result
       ▼
┌──────────────────────────────────────────────┐
│  进程 2：澄清路由 + 鼓励/排解                  │
│  - Kafka consumer (group: response-svc)       │
│  - confidence < 0.7 且轮次未耗尽 → 澄清 Agent │
│  - 否则 → Runner.run_streamed(ResponseAgent)  │
│  - token publish → Redis: stream:{request_id} │
│  - 最终结果 produce: chat.response            │
└──────┬───────────────────────────────────────┘
       │ publish: stream:{request_id}  (Redis Pub/Sub)
       │ produce: chat.response        (Kafka)
       ▼
     [ FastAPI 转发 SSE ]
```

---

## 核心特性

| 特性 | 说明 |
|---|---|
| **双 Agent 分离** | 情感识别 Agent 与鼓励/排解 Agent 独立定义，避免指令互相污染 |
| **结构化输出** | 情感识别用 Qwen JSON 模式输出，再由 Pydantic 严格校验 `sentiment` + `confidence`，杜绝自由文本解析错误 |
| **澄清回环** | 置信度低于阈值时先追问，最多 2 轮，防止无限循环 |
| **跨进程会话** | 基于 Redis 的 `Session` 实现，多个进程读写同一份历史，保证多轮语气连贯 |
| **Token 级流式** | 进程 2 使用 `Runner.run_streamed()`，token 经 Redis Pub/Sub 旁路推送，SSE 抵达前端 |
| **可靠通信** | Kafka 手动提交 offset + Redis `SET NX EX` 幂等认领 + 死信队列 |
| **可观测** | `trace()` 包裹，携带 `request_id` / `session_id`；配置 `OPENAI_API_KEY` 后可上传 OpenAI Dashboard |

---

## 技术栈

| 层 | 技术 |
|---|---|
| Agent 框架 | `openai-agents`（含 `openai-agents[redis]` 扩展） |
| 大模型 | Qwen（阿里云百炼 DashScope，OpenAI 兼容接口，默认 `qwen-plus`） |
| 消息队列 | Apache Kafka（`aiokafka` 异步客户端） |
| 会话存储 / Token 旁路 | Redis（`redis.asyncio`） |
| 后端服务 | FastAPI + Uvicorn（**单 worker**） |
| 前端交互 | `EventSource`（SSE） |
| 结构化校验 | Pydantic v2 |

---

## 项目结构

```
.
├── backend/
│   ├── __init__.py
│   ├── config.py                # 读取 .env 的统一配置
│   ├── llm.py                   # Qwen（DashScope）模型接入
│   ├── contracts.py             # 共享 Pydantic 消息模型
│   ├── sentiment_service.py     # 进程 1：情感识别
│   ├── response_service.py      # 进程 2：澄清路由 + 鼓励/排解 + 流式发布
│   └── api.py                   # FastAPI 后端：trigger + SSE stream 端点
├── frontend/
│   └── index.html               # 前端示例（EventSource 对接）
├── .env.example                 # 配置模板（复制为 .env 后填写）
├── .env                         # 本地配置，已被 .gitignore 忽略
├── Dockerfile                   # 三进程共用镜像
├── .dockerignore                # 排除 .env / .venv / logs 等
├── docker-compose.yml           # Kafka + Redis + 三个业务服务
├── requirements.txt
└── README.md
```

---

## 消息契约

所有进程共享 `backend/contracts.py` 中的 Pydantic 模型，Kafka 消息字段不允许在各进程内私自扩展。

### `ChatRequest` — `chat.request`

| 字段 | 类型 | 说明 |
|---|---|---|
| `request_id` | `str` | 由后端生成，全局唯一，用于串联全链路 |
| `session_id` | `str` | 会话标识，由前端在开启新对话时生成，跨轮保持不变 |
| `user_id` | `str` | 用户标识 |
| `message` | `str` | 用户原始输入 |
| `clarification_round` | `int` | 当前澄清轮次，初始 0，每轮澄清 +1 |
| `previous_clarification` | `str \| None` | 上一轮向用户提出的澄清问题（用于上下文） |

### `SentimentResult` — `sentiment.result`

| 字段 | 类型 | 说明 |
|---|---|---|
| `request_id` / `session_id` | `str` | 透传 |
| `message` | `str` | **原样透传用户输入**，供进程 2 的 Agent 看到原文 |
| `sentiment` | `"positive" \| "negative"` | 二分类结果 |
| `confidence` | `float [0,1]` | 模型置信度 |
| `clarification_round` | `int` | 透传 |

### `ChatResponse` — `chat.response`

| 字段 | 类型 | 说明 |
|---|---|---|
| `request_id` / `session_id` | `str` | 透传 |
| `type` | `"final" \| "clarification"` | 最终回复 or 澄清追问 |
| `reply` | `str` | 生成的文本 |
| `sentiment` / `confidence` | — | 透传情感识别结果 |

> `chat.response` 始终在流式结束后 produce 到 Kafka，用于审计、幂等与后续可能的重放；**token 流本身不经过 Kafka**。

---

## 环境准备

### 1. 依赖服务

```bash
# 本地起 Kafka + Redis（推荐 docker-compose）
docker compose up -d kafka redis
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

`requirements.txt`：

```
openai-agents[redis]>=0.0.7
aiokafka>=0.10
redis>=5.0
fastapi>=0.110
uvicorn[standard]>=0.29
pydantic>=2.6
python-dotenv>=1.0
```

### 3. 环境变量（.env）

```bash
cp .env.example .env
# 编辑 .env，至少填入 Qwen API Key：
# DASHSCOPE_API_KEY=sk-...
```

所有配置统一放在项目根目录 `.env`，由 `backend/config.py` 加载；同名环境变量优先级更高，适合在部署平台覆盖。

`.env` 主要配置：

```bash
DASHSCOPE_API_KEY=sk-...            # 必填，阿里云百炼 API Key
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_MODEL=qwen-plus
KAFKA_BOOTSTRAP=localhost:9092
REDIS_URL=redis://localhost:6379/0
```

---

## 配置项

集中在项目根目录 `.env`，由 `backend/config.py` 统一读取：

| 配置 | 默认值 | 说明 |
|---|---|---|
| `DASHSCOPE_API_KEY` | — | **必填**，Qwen（DashScope）API Key |
| `QWEN_BASE_URL` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | Qwen OpenAI 兼容接口地址 |
| `QWEN_MODEL` | `qwen-plus` | 使用的 Qwen 模型 |
| `CONFIDENCE_THRESHOLD` | `0.7` | 低于此置信度触发澄清 |
| `MAX_CLARIFICATION_ROUNDS` | `2` | 最大澄清轮次，达到后强制生成最终回复 |
| `SESSION_TTL_SECONDS` | `3600` | Redis 会话历史过期时间 |
| `LLM_TIMEOUT_SECONDS` | `120` | 模型调用 / FastAPI 等待兜底超时 |
| `LOG_LEVEL` | `INFO` | 日志级别 |

> **阈值调优建议**：先用标注数据跑进程 1，统计置信度分布，再确定阈值。阈值过高（如 0.85）会让正常输入频繁进入澄清，体验啰嗦；过低（如 0.5）则澄清机制形同虚设。

---

## 启动方式

分别启动三个进程（建议用 `tmux` / `supervisor` / systemd 管理）：

```bash
# 进程 1：情感识别
python -m backend.sentiment_service

# 进程 2：澄清 + 鼓励/排解
python -m backend.response_service

# 后端（单 worker，切勿加 --workers > 1）
uvicorn backend.api:app --host 0.0.0.0 --port 8000
```

> ⚠️ **后端必须单 worker 运行**。当前 `stream_queues`（`request_id → asyncio.Queue`）为进程内内存结构，多 worker 会导致 SSE 路由失败。进程 1 / 进程 2 可水平扩展多个实例（相同 `group_id` 的 consumer 会自动负载均衡），后端 QPS 压力由 Kafka 异步化吸收。

---

## Docker 部署

三个进程共用同一个镜像，只是启动命令不同；`docker-compose.yml` 同时管理 Kafka、Redis 与三个服务。

### 1. 构建镜像

```bash
docker build -t support-agent:latest .
```

镜像基于 `python:3.12-slim`，只包含 `backend/` 与 `frontend/`，以非 root 用户 `appuser` 运行；`.env`、日志、`.venv` 由 `.dockerignore` 排除，不会打进镜像。

### 2. 一条命令启动全栈

```bash
docker compose up -d --no-build
docker compose ps
docker compose logs -f api
```

> 本机 Compose v5 的 `docker compose build` 会因项目路径含中文触发 Buildx bake 报错（`x-docker-expose-session-sharedkey` 非 ASCII），因此先用上面的 `docker build` 构建，再以 `--no-build` 启动。更新代码后重复这两步即可。

启动后访问 http://localhost:8000 即可对话。三个容器的启动命令：

| 服务 | command |
|---|---|
| `sentiment` | `python -m backend.sentiment_service` |
| `response` | `python -m backend.response_service` |
| `api` | `uvicorn backend.api:app --host 0.0.0.0 --port 8000`（单 worker） |

### 3. 容器内配置

- `.env` 通过 compose 的 `env_file` 注入容器，Key 不打进镜像。
- 容器内 `KAFKA_BOOTSTRAP=kafka:29092`、`REDIS_URL=redis://redis:6379/0`，由 compose 的 `environment` 覆盖 `.env` 中的 `localhost` 值（同时设置 `DOTENV_OVERRIDE=false`，让编排层配置优先）。
- Kafka 对外仍暴露 `localhost:9092`，容器网络内走 `kafka:29092`（双监听器）。

### 4. 单独运行某个进程

```bash
docker run --rm --network support-net \
  --env-file .env \
  -e DOTENV_OVERRIDE=false \
  -e KAFKA_BOOTSTRAP=kafka:29092 \
  -e REDIS_URL=redis://redis:6379/0 \
  support-agent:latest python -m backend.sentiment_service
```

### 5. 停止 / 清理

```bash
docker compose down            # 停止并删除容器（保留镜像）
docker compose down -v         # 同时删除 Kafka / Redis 数据卷
```

> **端口冲突**：compose 会占用宿主机 `9092`（Kafka）和 `6379`（Redis）。如果本机已用 `brew services start kafka redis` 启动过，请先 `brew services stop kafka redis`，否则 `docker compose up` 会报端口被占用。两种方式二选一，不要同时运行。

---

## API 说明

### `POST /chat/trigger`

触发一次对话请求，立即返回 `request_id`。

**请求体**

```json
{
  "session_id": "sess-abc",
  "user_id": "u-1",
  "message": "今天工作压力好大",
  "clarification_round": 0,
  "previous_clarification": null
}
```

**响应**

```json
{ "request_id": "550e8400-e29b-41d4-a716-446655440000" }
```

### `GET /chat/stream/{request_id}`

建立 SSE 连接，接收该请求的 token 流。

**事件格式**（`data:` 后为 JSON）

```jsonc
// token 增量
{ "t": "我" }
{ "t": "理解" }

// 最终完成
{ "done": true, "full": "我理解你现在的感受……" }

// 澄清分支（非流式，一次性下发）
{ "type": "clarification", "reply": "能多说一点你现在的情绪吗？" }

// 错误
{ "error": "timeout" }
```

> `data:` 后跟空行（`\n\n`）是 SSE 协议要求，示例代码已包含。

---

## 澄清回环机制

```
用户输入 ─→ 情感识别 (confidence) ─→ confidence ≥ 0.7 ? ─是─→ 鼓励/排解 ─→ 结束
                                         │
                                        否
                                         │
                                         ▼
                               round < 2 ? ─是─→ 生成澄清问题 ─→ 前端展示 ─→ 用户补充
                                         │
                                        否
                                         │
                                         ▼
                                    走鼓励/排解（用当前最佳判断）
```

**设计要点**

1. **低置信度不是异常**：模型输出合法的 `positive`/`negative`，只是不够确定。这与 JSON 解析 / Pydantic 校验失败是不同性质的问题，前者走业务路由，后者走 DLQ。
2. **硬性轮次上限**：`MAX_CLARIFICATION_ROUNDS = 2`，达到后不再追问，避免用户陷入死循环。
3. **澄清问题不写入 Session**：只有 `type="final"` 的最终回复才通过 `RedisSession.add_items()` 持久化，避免污染鼓励/排解 Agent 的语气人设。
4. **前端配合**：收到 `type="clarification"` 后，用户下一次 `POST /chat/trigger` 时把 `clarification_round` 加 1，并携带 `previous_clarification`。

---

## 流式输出机制

### 为什么 token 不走 Kafka

Kafka 适合离散的可靠消息，不适合逐 token 的高频小消息。token 增量通过 **Redis Pub/Sub** 旁路，Kafka 只承载请求和最终结果。职责分离，各有其位。

### 进程 2 的流式消费

```python
result = Runner.run_streamed(agent, input_text, session=session)
async for event in result.stream_events():
    if event.type == "raw_response_event" \
       and isinstance(event.data, ResponseTextDeltaEvent):
        await redis_client.publish(
            f"stream:{request_id}",
            json.dumps({"type": "token", "token": event.data.delta}),
        )
```

> **必须完整消费 `stream_events()` 迭代器**，提前 `break` 会导致会话持久化后处理未完成、`is_complete` 状态不准确。

### FastAPI 的 SSE 转发

- 全局单例 `psubscribe("stream:*")`，按 `request_id` 路由到 `asyncio.Queue`。
- 必须设置响应头 `X-Accel-Buffering: no`，否则 Nginx 会缓冲整个响应体，SSE 退化为一次性返回。
- **不要使用 `BaseHTTPMiddleware`**，它会缓冲响应体导致 SSE 失效；如必须用中间件，用纯 ASGI 中间件。

### 客户端断开

SSE 生成器捕获 `asyncio.CancelledError` 后：
1. 在 Redis 写入 `cancel:{request_id}=1`（TTL 60s）。
2. 进程 2 流式循环定期检查该 key，命中则 `result.cancel()`，停止烧 token。

---

## 会话历史管理

使用 **OpenAI Agents SDK 内置 `RedisSession`**（`openai-agents[redis]` 提供），无需自研 `Session` 协议。

```python
import redis.asyncio as aioredis
from agents.extensions.memory import RedisSession

redis_client = aioredis.from_url(REDIS_URL)   # 进程启动时创建，全局复用

session = RedisSession(
    session_id=msg.session_id,
    redis_client=redis_client,     # 复用连接池，不要每次新建
    ttl=3600,                      # 1 小时记忆窗口
    key_prefix="support:session",
)
```

**关键约束**

- **进程 1 不传 Session**：情感识别只看当前这句话。
- **进程 2 必传 Session**：鼓励/排解需要历史上下文保持语气连贯。
- **澄清轮次不入库**：只有最终回复才写入历史。
- **`ttl` 必设**：否则 Redis 数据无限累积。
- **复用 Redis 客户端**：不要每次请求 `from_url` + `close()`，会耗尽连接池。

---

## 可靠性设计

### Kafka offset 提交时机

进程 1 / 进程 2 均关闭自动提交，**在 `send_and_wait()` 下游 topic 成功后才 `commit()`**。保证消息在下游确认前不会丢失。

### 幂等认领

进程 2 消费 `sentiment.result` 时，先原子性认领：

```python
async def claim_request(request_id: str) -> bool:
    key = f"processed:{request_id}"
    return await redis_client.set(key, "1", nx=True, ex=600) is not None
```

返回 `False` 表示已处理过，直接 `commit` 跳过。防止崩溃重投递导致用户看到重复回复。

### 死信队列（DLQ）

LLM 调用反复失败（限流、非法结构）时，重试 3 次后 produce 到：

- `sentiment.result.dlq`
- `chat.response.dlq`

**提交原 topic offset**，避免毒消息永久卡住分区。修复 bug 后可从 DLQ 重放。

### 分区顺序

producer 发送时以 `session_id` 作为消息 key：

```python
await producer.send_and_wait(topic, payload, key=session_id.encode())
```

同一会话的消息落同一分区，保证进程 2 按分区顺序消费，轮次不乱序。

---

## 可观测性

### Trace 包裹

SDK 的 Tracing 默认开启，但需主动注入业务标识才能按 `request_id` 检索：

```python
from agents import trace

with trace(
    workflow_name="support-pipeline",
    group_id=msg.session_id,
    metadata={"request_id": msg.request_id, "confidence": msg.confidence},
):
    # Agent 运行逻辑
```

> 使用 Qwen 时，trace 默认只保留本地 span（没有 `OPENAI_API_KEY` 不上传）。如需在 OpenAI Dashboard 查看，在 `.env` 中配置 `OPENAI_API_KEY`。

### Guardrails（建议后续接入）

进程 2 的鼓励/排解 Agent 输出前，可挂载 `output_guardrail` 检查是否包含不当内容（尤其对负面情绪用户说出刺激性话语）。

---

## 常见问题

**Q：为什么不能用 `--workers 4` 启动 FastAPI？**  
A：`stream_queues` 是进程内内存结构。多 worker 下，`chat.response` 可能被没有对应队列的 worker 消费而丢失，SSE 连接会挂起超时。水平扩展的职责交给 Kafka 消费端。若确需多 worker，需将 `pending` 与队列迁移到 Redis Pub/Sub 做跨进程路由。

**Q：为什么澄清问题不流式输出？**  
A：澄清问题通常是一句短文本，token 级体验收益有限，且流式与非流式混用会抬高前端复杂度。澄清分支一次性 publish `{"type": "clarification"}` 即可。

**Q：`confidence` 是模型自报的，可靠吗？**  
A：不可靠是常态，它是 LLM 对自身判断的粗略估计。**必须用标注数据校准阈值**，而非信任原始数值。若需要更严谨的置信度，可在情感识别后接一个轻量分类器（如 BERT）做二次打分，或让 Qwen 多次采样投票。

**Q：为什么情感识别用 JSON 模式，而不是 `output_type` / `json_schema`？**  
A：Qwen 目前只有部分模型支持 OpenAI 的 `json_schema` 严格模式；`qwen-plus` 等常用模型支持的是 `json_object`。因此情感识别 Agent 使用 JSON 模式输出，再由 Pydantic 严格校验字段与取值范围，兼容性更好，效果等价。

**Q：如何改用其他 Qwen 模型？**  
A：修改 `.env` 中的 `QWEN_MODEL` 即可（如 `qwen-turbo`、`qwen-max`），无需改代码。模型需支持 OpenAI 兼容的 Chat Completions 接口。

**Q：用户关闭页面后进程 2 会怎样？**  
A：SSE 断开触发前端 `source.close()`，后端生成器 `finally` 中清理队列并写入 `cancel:{request_id}`。进程 2 流式循环检测到取消标记后调用 `result.cancel()`，释放 LLM 资源。

**Q：能否不使用 Kafka，改用 HTTP 直连？**  
A：可以，但会失去消费端水平扩展、offset 回放、DLQ 等能力。当前设计选择 Kafka 是因为进程 1 / 进程 2 需要 **独立部署、独立扩缩容、独立故障隔离**。若规模较小，HTTP 直连是更简单的替代方案。

**Q：如何重放 DLQ 中的消息？**  
A：DLQ 是普通 Kafka topic。用任意 Kafka 客户端读取并 produce 回原 topic 即可。建议先修复导致失败的根因，再小批量重放验证。