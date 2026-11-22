以下为按新目录结构更新后的 `README.md` 内容，已将 `backend_api/` 改为 `backend/`，并将 `langgraph_impl/`、`agents_sdk_impl/` 放入 `backend/` 下。

---

# 情感支持对话 Demo（Kafka + FastAPI + Vue）

基于 Kafka 的多进程情感支持对话示例。前端收集用户输入，后端 FastAPI 接收请求并通过 Kafka 串联“情感识别”和“回复生成”两个独立进程。项目提供两套编排实现：

- `backend/langgraph_impl/`：使用 LangGraph 实现进程 1 和进程 2；
- `backend/agents_sdk_impl/`：使用 OpenAI Agents SDK 实现进程 1 和进程 2。

两套实现共享 FastAPI 后端、Vue 前端、Kafka Topic 和消息结构。同一时间只运行其中一套 worker。

> 当前版本不实现流式输出。前端提交后等待后端返回一次性结构化 JSON，并展示“情感识别结果 + 回复内容”。

---

## 1. 功能范围

### 已实现目标

- 前端：Vue 3 + Vite，用户输入文本并提交。
- 后端：FastAPI，接收前端请求，生产 Kafka 消息，等待处理结果并返回 JSON。
- 进程 1：消费 `raw_input`，调用 `qwen3-flash` 进行情感识别，只输出 `positive` 或 `negative`。
- 进程 2：消费情感识别结果，正向生成鼓励回复，负向生成排解回复。
- 进程间通信：Kafka。
- 两套独立实现：LangGraph 版、OpenAI Agents SDK 版。
- 模型接入：通过 `.env` 提供 `API Key` 和 `base_url`，使用 OpenAI 兼容接口调用 `qwen3-flash`。
- 展示：前端显示情感标签和回复文本。

### 当前不包含

- 不实现流式输出；
- 不支持多轮会话和历史记忆；
- 不处理中性情感，只支持 `positive / negative`；
- 暂不包含心理危机、自伤自杀等高风险内容的专门安全分流；
- 不需要单元测试、Mock 测试、Docker；
- 不包含用户系统和数据库持久化。

---

## 2. 总体架构

```text
┌────────────────────┐
│ Vue 前端            │
│ 用户输入 / 展示结果  │
└─────────┬──────────┘
          │ POST /api/chat
          ▼
┌────────────────────┐
│ FastAPI 后端进程     │
│ backend/main.py     │
│ 接收请求             │
│ 生产 raw_input       │
│ 消费 sentiment_result│
│ 按 request_id 匹配   │
│ 返回结构化 JSON      │
└─────────┬──────────┘
          │ produce
          ▼
┌────────────────────┐
│ Kafka Topic         │
│ raw_input           │
└─────────┬──────────┘
          │ consume
          ▼
┌────────────────────┐
│ 进程 1：情感识别     │
│ LangGraph 或 Agents │
│ 调用 qwen3-flash     │
│ 输出 positive/negative│
└─────────┬──────────┘
          │ produce
          ▼
┌────────────────────┐
│ Kafka Topic         │
│ sentiment_result    │
│ type=sentiment      │
└─────────┬──────────┘
          │ consume
          ▼
┌────────────────────┐
│ 进程 2：回复生成     │
│ 正向 -> 鼓励         │
│ 负向 -> 排解         │
│ 调用 qwen3-flash     │
└─────────┬──────────┘
          │ produce
          ▼
┌────────────────────┐
│ Kafka Topic         │
│ sentiment_result    │
│ type=reply          │
└─────────┬──────────┘
          │ consume
          ▼
┌────────────────────┐
│ FastAPI 后端进程     │
│ 组装 sentiment+reply │
│ 返回给前端           │
└────────────────────┘
```

---

## 3. Kafka Topic 设计

当前只使用两个 Topic：

| Topic              | 生产者         | 消费者               | 说明                           |
| ------------------ | -------------- | -------------------- | ------------------------------ |
| `raw_input`        | FastAPI 后端   | 进程 1               | 用户原始输入                   |
| `sentiment_result` | 进程 1、进程 2 | 进程 2、FastAPI 后端 | 承载情感结果和最终回复两类消息 |

为了只使用两个 Topic，`sentiment_result` 中通过 `type` 字段区分消息类型：

- `type=sentiment`：进程 1 输出的情感识别结果；
- `type=reply`：进程 2 输出的最终回复。

所有消费者必须按 `type` 过滤，避免误处理或循环处理。

### 消费者组建议

| 消费者       | group id           | 过滤规则                                                     |
| ------------ | ------------------ | ------------------------------------------------------------ |
| 进程 1       | `sentiment-worker` | 只消费 `raw_input`                                           |
| 进程 2       | `reply-worker`     | 只处理 `sentiment_result` 中 `type=sentiment`                |
| FastAPI 后端 | `backend-api`      | 处理 `sentiment_result` 中 `type=sentiment` 和 `type=reply`，按 `request_id` 聚合 |

> 同一时间只启动 LangGraph 版或 Agents SDK 版的一套 worker。若两套同时运行且使用相同 group id，会发生消息分摊，导致部分请求由不同实现处理。若确需同时运行，请为两套实现配置不同的 group id 前缀。

### 分区和顺序

建议创建单分区 Topic：

- `raw_input`：1 个分区；
- `sentiment_result`：1 个分区。

生产消息时以 `request_id` 作为 Kafka message key，确保同一请求的消息进入同一分区并保持顺序。单分区场景下，后端会先收到 `type=sentiment`，再收到 `type=reply`。

---

## 4. 目录结构

```text
project/
├── .env.example
├── requirements.txt
├── README.md
├── shared/
│   ├── __init__.py
│   ├── kafka_config.py          # Kafka 生产者/消费者工厂
│   └── schemas.py               # Pydantic 消息模型
├── backend/
│   ├── __init__.py
│   ├── main.py                  # FastAPI 入口、/api/chat、/health
│   ├── kafka_bridge.py          # 请求生产与结果消费聚合
│   ├── langgraph_impl/
│   │   ├── __init__.py
│   │   ├── sentiment_worker.py  # 进程 1
│   │   ├── reply_worker.py      # 进程 2
│   │   └── graphs/
│   │       ├── __init__.py
│   │       ├── sentiment_graph.py
│   │       └── reply_graph.py
│   └── agents_sdk_impl/
│       ├── __init__.py
│       ├── sentiment_worker.py  # 进程 1
│       ├── reply_worker.py      # 进程 2
│       └── agents/
│           ├── __init__.py
│           ├── sentiment_agent.py
│           └── reply_agent.py
└── frontend/
    ├── package.json
    ├── vite.config.js
    └── src/
        ├── App.vue
        └── main.js
```

---

## 5. 环境要求

- Python 3.11+
- Node.js 18+
- 可访问的 Kafka 集群，或本地安装的 Kafka
- 可访问的 `qwen3-flash` OpenAI 兼容接口

---

## 6. 环境变量

在项目根目录创建 `.env`，参考 `.env.example`：

```env
# LLM 配置
OPENAI_API_KEY=your_api_key
OPENAI_BASE_URL=https://your-qwen-compatible-endpoint/v1
QWEN_MODEL=qwen3-flash
LLM_TEMPERATURE=0.7
LLM_MAX_TOKENS=512

# Kafka 配置
KAFKA_BOOTSTRAP_SERVERS=localhost:9092
RAW_INPUT_TOPIC=raw_input
SENTIMENT_RESULT_TOPIC=sentiment_result

# 消费者组
SENTIMENT_WORKER_GROUP_ID=sentiment-worker
REPLY_WORKER_GROUP_ID=reply-worker
BACKEND_GROUP_ID=backend-api

# 后端等待回复超时时间，单位秒
REQUEST_TIMEOUT_SECONDS=60
```

说明：

- `OPENAI_BASE_URL` 必须指向 qwen3-flash 的 OpenAI 兼容接口；
- LangGraph 版使用 `langchain-openai` 的 `ChatOpenAI`，配置 `base_url` 和 `api_key`；
- OpenAI Agents SDK 版建议使用 Chat Completions 兼容模型封装，避免依赖 Responses API；
- 情感识别建议关闭 thinking 或降低温度，减少延迟和输出波动。

---

## 7. Kafka 初始化

确保 Kafka 已启动。若为本地 Kafka，创建两个 Topic：

```bash
kafka-topics.sh \
  --bootstrap-server localhost:9092 \
  --create \
  --topic raw_input \
  --partitions 1 \
  --replication-factor 1

kafka-topics.sh \
  --bootstrap-server localhost:9092 \
  --create \
  --topic sentiment_result \
  --partitions 1 \
  --replication-factor 1
```

查看 Topic：

```bash
kafka-topics.sh --bootstrap-server localhost:9092 --list
```

如果使用云 Kafka，请替换 `KAFKA_BOOTSTRAP_SERVERS`，并按云厂商要求配置认证。当前版本不包含 SASL/SSL 配置示例。

---

## 8. 安装依赖

### 8.1 Python 依赖

`requirements.txt` 建议内容：

```txt
fastapi
uvicorn[standard]
aiokafka
pydantic
python-dotenv
openai
langgraph
langchain-openai
openai-agents
```

安装：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Windows 激活命令：

```powershell
.venv\Scripts\activate
```

### 8.2 前端依赖

```bash
cd frontend
npm install
```

---

## 9. 启动方式

启动顺序建议：

1. Kafka；
2. FastAPI 后端；
3. 选择一套 worker：LangGraph 版或 OpenAI Agents SDK 版；
4. Vue 前端。

### 9.1 启动 FastAPI 后端

在项目根目录：

```bash
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

健康检查：

```bash
curl http://localhost:8000/health
```

### 9.2 启动 LangGraph 版 worker

新开两个终端，分别启动进程 1 和进程 2：

```bash
python -m backend.langgraph_impl.sentiment_worker
```

```bash
python -m backend.langgraph_impl.reply_worker
```

### 9.3 启动 OpenAI Agents SDK 版 worker

先停止 LangGraph 版 worker，再新开两个终端：

```bash
python -m backend.agents_sdk_impl.sentiment_worker
```

```bash
python -m backend.agents_sdk_impl.reply_worker
```

> 不要同时启动两套 worker，除非已为它们配置不同的 Kafka 消费者组。

### 9.4 启动 Vue 前端

```bash
cd frontend
npm run dev
```

默认访问 `http://localhost:5173`。Vite 需配置代理，将 `/api` 转发到 `http://localhost:8000`。

`vite.config.js` 代理示例：

```js
export default {
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true
      }
    }
  }
}
```

FastAPI 需允许前端开发地址跨域，或使用 Vite 代理避免 CORS。

---

## 10. API 说明

### `POST /api/chat`

请求体：

```json
{
  "message": "今天工作好累，感觉什么都做不好"
}
```

成功响应，非流式，一次性返回：

```json
{
  "request_id": "8f1c2c9e-2c4d-4f2a-9a8e-123456789abc",
  "input": "今天工作好累，感觉什么都做不好",
  "sentiment": "negative",
  "reply": "听起来你今天承受了不少压力。先允许自己休息一下，不需要马上把所有事情都做好。"
}
```

字段说明：

| 字段         | 类型   | 说明                        |
| ------------ | ------ | --------------------------- |
| `request_id` | string | 本次请求唯一 ID             |
| `input`      | string | 用户原始输入                |
| `sentiment`  | string | `positive` 或 `negative`    |
| `reply`      | string | 进程 2 生成的鼓励或排解回复 |

错误响应示例：

```json
{
  "detail": "处理超时，请稍后重试"
}
```

建议状态码：

- `200`：成功；
- `422`：请求体不合法；
- `502`：LLM 调用或消息处理失败；
- `504`：等待 Kafka 结果超时。

### `GET /health`

返回：

```json
{
  "status": "ok"
}
```

---

## 11. Kafka 消息格式

### 11.1 `raw_input`

FastAPI 后端生产：

```json
{
  "request_id": "8f1c2c9e-2c4d-4f2a-9a8e-123456789abc",
  "text": "今天工作好累，感觉什么都做不好",
  "created_at": "2026-11-19T10:00:00Z"
}
```

Kafka message key：`request_id`。

### 11.2 `sentiment_result`，`type=sentiment`

进程 1 生产：

```json
{
  "request_id": "8f1c2c9e-2c4d-4f2a-9a8e-123456789abc",
  "type": "sentiment",
  "text": "今天工作好累，感觉什么都做不好",
  "sentiment": "negative",
  "created_at": "2026-11-19T10:00:01Z"
}
```

约束：

- `sentiment` 只允许 `positive` 或 `negative`；
- 进程 1 应使用结构化输出或严格 JSON 解析；
- 若模型返回其他值，应重试或按错误处理，不要写入非法标签。

### 11.3 `sentiment_result`，`type=reply`

进程 2 生产：

```json
{
  "request_id": "8f1c2c9e-2c4d-4f2a-9a8e-123456789abc",
  "type": "reply",
  "sentiment": "negative",
  "reply": "听起来你今天承受了不少压力。先允许自己休息一下，不需要马上把所有事情都做好。",
  "created_at": "2026-11-19T10:00:03Z"
}
```

FastAPI 后端按 `request_id` 匹配 `type=sentiment` 和 `type=reply`，收到 `type=reply` 后返回完整 JSON。

---

## 12. backend 下两套编排实现说明

### 12.1 LangGraph 版

目录：`backend/langgraph_impl/`

- 进程 1：
  - 消费 `raw_input`；
  - 使用 LangGraph 单节点图；
  - 通过 `ChatOpenAI` 调用 qwen3-flash；
  - 使用结构化输出约束 `sentiment`；
  - 生产 `type=sentiment` 到 `sentiment_result`。
- 进程 2：
  - 消费 `sentiment_result` 中 `type=sentiment`；
  - 根据标签选择鼓励或排解提示词；
  - 使用 LangGraph 图或单节点生成回复；
  - 生产 `type=reply` 到 `sentiment_result`。

启动命令：

```bash
python -m backend.langgraph_impl.sentiment_worker
python -m backend.langgraph_impl.reply_worker
```

### 12.2 OpenAI Agents SDK 版

目录：`backend/agents_sdk_impl/`

- 进程 1：
  - 消费 `raw_input`；
  - 使用 Agents SDK Agent，配置结构化输出模型；
  - 调用 qwen3-flash OpenAI 兼容接口；
  - 生产 `type=sentiment`。
- 进程 2：
  - 消费 `sentiment_result` 中 `type=sentiment`；
  - 根据情感选择鼓励 Agent 或排解 Agent；
  - 生成回复并生产 `type=reply`。

启动命令：

```bash
python -m backend.agents_sdk_impl.sentiment_worker
python -m backend.agents_sdk_impl.reply_worker
```

注意：Agents SDK 默认可能使用 OpenAI Responses API。由于 qwen3-flash 主要兼容 Chat Completions，需使用 Chat Completions 兼容模型配置或自定义模型封装。

---

## 13. 前端说明

前端建议包含：

- 一个文本输入框；
- 一个“发送”按钮；
- 加载状态；
- 结果区域分两块展示：
  - 情感识别结果：`positive` 显示为“正向”，`negative` 显示为“负向”；
  - 回复内容：显示进程 2 生成文本。

由于当前非流式，提交后前端显示 loading，等待 `/api/chat` 返回后一次性渲染结果。

---

## 14. 错误处理与超时

建议实现：

- 后端为每个 `request_id` 创建等待 Future；
- 收到 `type=sentiment` 时缓存情感结果；
- 收到 `type=reply` 时完成 Future 并返回；
- 超过 `REQUEST_TIMEOUT_SECONDS` 返回 `504`；
- LLM 调用失败时记录日志，进程 1 或进程 2 不写入非法消息；
- 后端返回 `502` 并提示稍后重试；
- Kafka 不可用时，后端启动或请求失败应返回明确错误。

---

## 15. 限制与后续扩展

当前版本的已知限制：

- 只支持正向和负向，不支持中性；
- 不包含多轮对话；
- 不包含危机干预；
- 不包含流式输出；
- 不包含持久化和用户系统；
- `sentiment_result` Topic 同时承载情感结果和回复消息，属于在“两个 Topic”约束下的折中设计。

后续可扩展：

- 增加 `reply_stream` Topic 实现流式输出；
- 增加 `neutral` 情感和混合情感；
- 增加高风险内容检测和求助建议；
- 增加会话历史；
- 增加重试、死信队列和监控；
- 将 Kafka 认证配置化。

---

## 16. 快速验收

1. 启动 Kafka 并创建两个 Topic；
2. 启动 FastAPI 后端：`uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload`；
3. 启动任意一套 worker 的进程 1 和进程 2；
4. 启动 Vue 前端；
5. 输入“今天工作好累，感觉什么都做不好”；
6. 前端应显示情感识别结果为 `negative`，并展示排解回复；
7. 输入“今天和朋友一起吃饭很开心”；
8. 前端应显示情感识别结果为 `positive`，并展示鼓励回复。

---

## 17. 安全声明

本项目是技术示例，用于演示 Kafka 多进程编排和 LLM 情感支持回复生成，不是心理治疗或医疗系统。当前版本不处理自伤、自杀、暴力等高风险内容。生产使用前必须补充安全策略、人工求助引导、内容审核和合规评估。