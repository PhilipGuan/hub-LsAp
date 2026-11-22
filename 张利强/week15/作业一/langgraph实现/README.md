# Emotion Chat — 基于 LangGraph + Kafka 的情感识别对话系统

一个本地可运行的演示项目：用户在前端输入消息，后端通过 **Kafka** 将请求分发给两个**操作系统级独立进程**（情感识别进程、回复生成进程），利用 **Qwen 大模型**完成情感判断与自然语言回复，最终通过 **SSE** 向前端逐字流式返回。对话历史由 **LangGraph + AsyncSqliteSaver** 按回合持久化。

---

## 1. 项目特性

- **三进程架构**：FastAPI 后端、情感识别进程、回复生成进程彼此独立，通过 Kafka 解耦。
- **情感识别**：输出 `positive / negative / neutral` 三分类，附带 `confidence`（置信度）、`intensity`（情绪强度）、`mixed_emotions`（混合情绪）。
- **策略化回复**：
  - `positive` → 鼓励
  - `negative` → 排解
  - `neutral` → 普通回应 / 继续引导
- **流式体验**：前端先收到 `emotion` 事件（负面时显示“检测到负面情绪”横幅），随后逐字接收 `token` 事件。
- **回合级状态持久化**：LangGraph 在每回合结束后将完整状态写入 SQLite，避免 token 级写入带来的性能问题。
- **降级兜底**：情感识别失败降级为 `neutral`；回复生成失败返回兜底话术；所有异常均记录结构化日志。
- **本地 demo 优先**：只需一个 Kafka broker、Qwen API Key、Python 3.12，无需额外中间件。

---

## 2. 架构总览

```
┌──────────┐   POST /chat (SSE)   ┌──────────────────────┐
│  前端 Web │ ◄──────────────────► │   FastAPI 后端进程    │
└──────────┘                      │  ├─ Kafka Producer   │
                                  │  ├─ 结果消费者 (路由) │
                                  │  └─ LangGraph (回合级)│
                                  └──────┬───────┬───────┘
                                         │       │
                          emotion-request│       │reply-request
                                         ▼       ▼
                              ┌──────────────┐ ┌──────────────┐
                              │ 进程1 情感识别 │ │ 进程2 回复生成 │
                              └──────┬───────┘ └──────┬───────┘
                                     │                │
                          emotion-result      reply-token (逐 token)
                                     │                │
                                     └───────┬────────┘
                                             ▼
                                    Kafka (本地 broker)
                                             │
                                             ▼
                                    FastAPI → SSE → 前端
                                             │
                                             ▼
                              LangGraph Checkpointer → SQLite
```

### 核心设计原则

1. **Kafka 仅用于后端内部通信**，前端只与 FastAPI 交互（SSE）。
2. **LangGraph 只做回合级编排**：图在每回合结束时写入一次完整状态；流式 token 的转发发生在 LangGraph 之外。
3. **请求-回复模式**：Kafka 原生是发布-订阅，通过 `correlation_id` + 进程内 `asyncio.Queue` 注册表实现请求-回复语义。
4. **过程流式（含义 A）** ：情感识别进程内部用 `stream=True` 调用 Qwen，但对外只发布一次完整的 JSON 结果；前端不需要处理部分字段到达。
5. **至少一次语义**：Kafka 不做事务，重复消息由 `correlation_id` 幂等处理。

---

## 3. 技术栈

| 层 | 选型 |
|---|---|
| 语言 | Python 3.12 |
| Web 框架 | FastAPI + Uvicorn |
| 流式传输 | SSE (`text/event-stream`) |
| 消息总线 | Apache Kafka + `aiokafka` |
| 图编排 | LangGraph |
| 状态持久化 | `langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver` |
| 大模型 | Qwen（DashScope OpenAI 兼容接口，`qwen-plus`） |
| 前端 | 原生 HTML + Fetch ReadableStream |

---

## 4. 目录结构

```
emotion-chat/
├── README.md
├── requirements.txt
├── .env.example
├── docker-compose.yml          # 可选：本地 Kafka
├── backend/
│   ├── main.py                 # FastAPI 入口、lifespan、/chat
│   ├── kafka_bridge.py         # Producer/Consumer 封装、correlation 路由
│   ├── graph.py                # LangGraph 图定义 + checkpointer 装配
│   └── sse.py                  # SSE 格式化与流生成器
├── workers/
│   ├── emotion_worker.py       # 进程1：情感识别
│   └── reply_worker.py         # 进程2：回复生成
├── frontend/
│   ├── index.html
│   └── app.js
└── data/
    └── checkpoints.sqlite      # 运行时生成
```

---

## 5. Kafka 主题与消息 Schema

| 主题 | 生产者 | 消费者 | 说明 |
|---|---|---|---|
| `emotion-request` | FastAPI 后端 | 进程1 | 情感识别请求 |
| `emotion-result` | 进程1 | FastAPI 后端 | 情感识别结果 |
| `reply-request` | FastAPI 后端 | 进程2 | 回复生成请求 |
| `reply-token` | 进程2 | FastAPI 后端 | 逐 token 回复流 |

消费者组：`backend-emotion-group`、`backend-reply-group`、`emotion-worker`、`reply-worker`（互不重叠，避免重复消费）。

### 消息体定义

**emotion-request**
```json
{
  "correlation_id": "uuid",
  "user_input": "今天面试又挂了……",
  "history": []            // 可选，最近 N 轮
}
```

**emotion-result**
```json
{
  "correlation_id": "uuid",
  "label": "negative",                 // positive | negative | neutral
  "confidence": 0.92,
  "intensity": "high",                 // low | medium | high
  "mixed_emotions": [
    {"label": "frustration", "score": 0.7},
    {"label": "sadness", "score": 0.5}
  ],
  "error": null
}
```

**reply-request**
```json
{
  "correlation_id": "uuid",
  "user_input": "今天面试又挂了……",
  "emotion_result": { "...emotion-result 对象..." },
  "history": []
}
```

**reply-token**
```json
{
  "correlation_id": "uuid",
  "token": "我",
  "is_final": false,
  "error": null
}
```
`is_final: true` 表示本回合回复结束（`token` 为空字符串）；若 `error` 非空则为失败终止信号。

---

## 6. 接口说明

### `POST /chat`

**请求体**
```json
{
  "message": "今天面试又挂了……",
  "user_id": "u1",
  "conversation_id": "c1"
}
```

**响应**：`text/event-stream`，事件类型如下：

| event | data 示例 | 说明 |
|---|---|---|
| `emotion` | `{"label":"negative","confidence":0.92,"intensity":"high","mixed_emotions":[...]}` | 情感识别结果；`negative` 时前端显示“检测到负面情绪” |
| `token` | `{"token":"我"}` | 回复的一个增量片段 |
| `done` | `{"reply":"完整回复","debug":{"correlation_id":"...","thread_id":"u1:c1"}}` | 回复结束 + 调试信息 |
| `error` | `{"message":"处理超时，请重试"}` | 超时或异常终止 |

**SSE 时序**：`emotion` → `token` × N → `done`（或 `error`）。

---

## 7. 状态模型（LangGraph State）

```python
class TurnState(TypedDict):
    user_input: str
    emotion_label: str
    confidence: float | None
    intensity: str | None
    mixed_emotions: list[dict] | None
    reply: str
```

- 图结构极简：单节点 `record` → `END`。
- `thread_id` 使用 **`f"{user_id}:{conversation_id}"`** 复合键，防止不同用户 / 会话状态串扰。
- 持久化时机：**每回合结束后**（`is_final` 收到后）由后端调用 `_record_turn` 一次性写入。
- 存储位置：`data/checkpoints.sqlite`。

> 说明：LangGraph 在这里承担"状态管理层"角色；实时流式 token 不进入图状态，避免 SQLite 写放大。

---

## 8. 快速开始

### 8.1 环境准备

- Python 3.12
- 一个本地 Kafka broker（推荐用 Docker Compose 一键启动）
- Qwen API Key（[DashScope 控制台](https://dashscope.console.aliyun.com/) 获取）

### 8.2 启动 Kafka（可选 Docker 方式）

```bash
docker compose up -d
# 默认监听 localhost:9092
```

`docker-compose.yml` 示例（KRaft 单节点）：
```yaml
services:
  kafka:
    image: bitnami/kafka:3.7
    ports: ["9092:9092"]
    environment:
      KAFKA_CFG_NODE_ID: "0"
      KAFKA_CFG_PROCESS_ROLES: "controller,broker"
      KAFKA_CFG_CONTROLLER_QUORUM_VOTERS: "0@kafka:9093"
      KAFKA_CFG_LISTENERS: "PLAINTEXT://:9092,CONTROLLER://:9093"
      KAFKA_CFG_ADVERTISED_LISTENERS: "PLAINTEXT://localhost:9092"
      KAFKA_CFG_LISTENER_SECURITY_PROTOCOL_MAP: "CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT"
      KAFKA_CFG_CONTROLLER_LISTENER_NAMES: "CONTROLLER"
```

### 8.3 安装依赖

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`requirements.txt` 参考：
```
fastapi>=0.115
uvicorn[standard]>=0.30
aiokafka>=0.11
langgraph>=0.2
langgraph-checkpoint-sqlite>=2.0
aiosqlite>=0.20
openai>=1.50
pydantic>=2.8
python-dotenv>=1.0
```

### 8.4 配置环境变量

复制 `.env.example` 为 `.env`：

```bash
# .env.example
KAFKA_BOOTSTRAP=localhost:9092
QWEN_API_KEY=sk-xxxxxxxx
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_MODEL=qwen-plus
EMOTION_TIMEOUT=15
REPLY_TIMEOUT=30
SQLITE_PATH=data/checkpoints.sqlite
```

### 8.5 启动三个进程

分别在三个终端中启动（或使用进程管理器）：

```bash
# 终端 1：进程1 情感识别
python -m workers.emotion_worker

# 终端 2：进程2 回复生成
python -m workers.reply_worker

# 终端 3：FastAPI 后端
uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

### 8.6 打开前端

用任意静态服务器托管 `frontend/`（或直接 `python -m http.server`），将 `app.js` 中的接口地址指向 `http://localhost:8000/chat`。

---

## 9. 关键实现片段

### 9.1 SSE 事件格式化

```python
def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
```

### 9.2 correlation_id 路由

```python
_pending: dict[str, asyncio.Queue] = {}

# SSE 生成器内注册
queue = asyncio.Queue()
_pending[correlation_id] = queue

# 后台消费者
async def _consume(topic, group):
    consumer = AIOKafkaConsumer(topic, group_id=group, ...)
    await consumer.start()
    async for msg in consumer:
        q = _pending.get(msg.value.get("correlation_id"))
        if q:
            await q.put(msg.value)
```

> 客户端断开时，生成器 `finally` 中清理 `_pending`；重复消息由 `correlation_id` 幂等丢弃。

### 9.3 LangGraph 回合写入

```python
async def _record_turn(thread_id, user_input, emotion, reply):
    await _compiled_graph.ainvoke(
        {
            "user_input": user_input,
            "emotion_label": emotion["label"],
            "confidence": emotion.get("confidence"),
            "intensity": emotion.get("intensity"),
            "mixed_emotions": emotion.get("mixed_emotions"),
            "reply": reply,
        },
        config={"configurable": {"thread_id": thread_id}},
    )
```

`AsyncSqliteSaver` 在 `lifespan` 中以 `async with` 方式持有，确保连接正确关闭。

---

## 10. 降级与日志策略

| 场景 | 行为 | 前端可见性 |
|---|---|---|
| 情感识别超时 / 异常 | 降级为 `neutral`，`debug.emotion_degraded=True` | 收到 `emotion` 事件（neutral）；流继续 |
| 回复生成超时 / 异常 | 发布兜底话术（如“我暂时没能生成回复，但我在听”），`debug.reply_degraded=True` | 收到兜底 `token` + `done` |
| 整体超时 | SSE 发送 `error` 事件后结束 | 提示“处理超时，请重试” |
| 客户端提前断开 | 生成器 `finally` 清理 `_pending`，不再写入 DB | — |

**日志字段建议**：`request_id` / `correlation_id` / `thread_id` / 进程1耗时 / 进程2耗时 / 是否降级 / 错误类型。不持久化完整用户原文，避免隐私风险。

> ⚠️ 本项目**不做**危机内容特殊处理（如自伤 / 极端负面）。如需上线，请务必补充安全策略。

---

## 11. 设计决策记录（ADR 摘要）

| 决策 | 取舍 |
|---|---|
| Kafka 仅用于后端内部 | 前端复杂度低，SSE 足以支撑流式 |
| 情感识别"过程流式"（含义 A） | 实现简单，前端只需处理完整 JSON |
| LangGraph 回合级编排（选项 X） | 避免 token 级 SQLite 写入；中断/恢复机制不在 demo 范围内 |
| `correlation_id` + `asyncio.Queue` 路由 | Kafka 无原生请求-回复，用进程内注册表最轻量 |
| `thread_id = user_id:conversation_id` | 防会话串扰 |
| SQLite 而非 MemorySaver | 重启后历史可恢复；单文件部署零依赖 |

---

## 12. 已知限制

- 单机 demo 架构，Kafka / FastAPI / 两个 worker 均假设**单实例**；多实例部署需将 `_pending` 路由迁移到 Redis 等共享存储。
- 使用 `at-least-once` 语义，极端重复情况下同一回合可能被重放，由 `correlation_id` 幂等兜底。
- 情感识别使用固定字段顺序 prompt；若 Qwen 输出顺序漂移，需在解析层做容错。
- 未接入鉴权、限流、审计；**不建议直接暴露公网**。
- 未处理危机内容，仅为技术演示。

---

## 13. 后续可扩展方向

- 将 `_pending` 路由迁移到 Redis，支持多 worker 水平扩展。
- 情感识别升级为"含义 B"（字段增量推送），进一步降低首字延迟。
- 接入 LangGraph 中断/恢复，实现真正事件驱动的图执行。
- 增加危机内容检测与安全兜底。
- Checkpointer 迁移到 Postgres / Redis，适配生产部署。

---

## 14. 许可证

本项目为演示用途，采用 MIT License。