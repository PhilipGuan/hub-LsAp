# 多 Agent 情感交互系统 · LangGraph 版

本项目通过两个 Agent 完成文本情感对话。情绪识别 Agent 判断用户当前消息的情绪类别，情感回应 Agent 结合识别结果和同一会话的历史，生成鼓励、安慰或自然回应。

**当前已接入 Streamlit 聊天、FastAPI 多轮接口和 SQLite 历史恢复。**

## 技术选型

| 配置项 | V1.0 方案 |
| --- | --- |
| Agent 1 · 情绪识别 | 小米 MiMo-V2.6-Flash（`mimo-v2.6-flash`） |
| Agent 2 · 情感回应 | 小米 MiMo-V2.6-Flash（`mimo-v2.6-flash`） |
| Agent 编排 | LangGraph StateGraph |
| 模型接口 | 小米 MiMo 提供的 OpenAI 兼容 Chat Completions API |
| 情绪识别结果 | 经 Pydantic 校验的结构化数据 |
| 前端 | Streamlit |
| 后端 | FastAPI，非流式返回完整回复 |
| 持久化 | SQLite，用于保存聊天历史 |

## 对话流程

1. 用户在 Streamlit 中发送消息。FastAPI 根据会话 ID 创建或读取会话，并加载该会话的历史消息。
2. Agent 1 在每一轮重新识别当前消息，输出正面、负面或中性的情绪倾向，以及细分情绪标签。细分标签可以是喜悦、焦虑、沮丧等，完整枚举在实现时定义。
3. Agent 2 结合当前消息、通过校验的情绪结果和历史上下文，生成适合当前对话的回复。检测到高风险表达时，后端进入专门的回应流程。
4. 后端将本轮用户消息和回复写入 SQLite，再一次性返回完整回复。程序重启后可按原会话 ID 恢复聊天历史。

Streamlit 默认只显示聊天界面。开启调试模式后，额外展示两个 Agent 的执行状态和本轮情绪识别结果。

## 模型接入与结构化输出

LangGraph 的两个节点使用 `AsyncOpenAI` 调用小米 MiMo 的 OpenAI 兼容接口。`MIMO_API_KEY` 使用小米开放平台签发的密钥。 [MiMo-V2.6-Flash 官方模型页](https://mimo.mi.com/models/en-US/mimo-v2.6-flash)

情绪识别节点先按 Pydantic 模型发送 `json_schema` 请求。若 MiMo 拒绝该输出格式，则用 `json_object` 重试，并在后端校验结果。两个节点均以非流式方式使用 `mimo-v2.6-flash`。 [MiMo 结构化输出文档](https://mimo.mi.com/docs/en-US/quick-start/usage-guide/text-generation/structured-output)

## 异常与高风险表达处理

| 场景 | 预期处理 |
| --- | --- |
| MiMo API 鉴权失败、超时、限流或服务异常 | 后端记录错误类型，前端显示易懂的失败提示。对可恢复的错误提供重试入口。 |
| 情绪输出无法解析或未通过 Pydantic 校验 | 标记本轮识别失败，按预设策略重试。仍失败时采用通用回应，并在调试模式显示识别失败。 |
| 用户表达自伤等高风险信号 | 优先提供简短、支持性的安全回应，并引导用户联系可信任的人或当地紧急援助。该流程在情绪分类失败时也应可用。 |

## 目录骨架与计划文件

目录树以当前项目根目录为起点。`frontend/`、`backend/`、`data/`、`tests/` 及列出的后端子目录已创建，业务文件随对应模块实现加入。

```text
./
├── frontend/
│   └── app.py
├── backend/
│   ├── main.py
│   ├── agents/
│   │   ├── _mimo.py
│   │   ├── emotion_agent.py
│   │   └── response_agent.py
│   ├── services/
│   │   ├── chat_service.py
│   │   └── session_service.py
│   ├── models/
│   │   └── schemas.py
│   └── database/
│       └── storage.py
├── data/
├── tests/
├── .env.example
├── AGENTS.md
├── requirements.txt
└── README.md
```

## 模块接口

| 计划文件 | 输入与输出 |
| --- | --- |
| `frontend/app.py` | 向 FastAPI 提交当前消息和已有会话 ID，展示会话历史与完整回复。 |
| `backend/main.py` | 校验聊天请求并交给聊天服务，返回会话 ID 和完整回复。启用调试模式时附带执行状态和情绪结果。 |
| `backend/services/chat_service.py` | 使用传入的历史编排情绪识别、情感回应及异常处理。 |
| `backend/agents/emotion_agent.py` | 接收当前消息，返回通过 Pydantic 校验的情绪倾向与细分标签，或报告识别失败。 |
| `backend/agents/response_agent.py` | 接收当前消息、同一会话的已有历史和有效的情绪结果，返回完整回复文本。 |
| `backend/models/schemas.py` | 定义情绪结果以及 FastAPI 请求、响应所用的数据结构。 |
| `backend/services/session_service.py`、`backend/database/storage.py` | 会话服务加载历史、执行聊天并保存整轮消息，存储层负责 SQLite 读写。 |

分类持续失败时，由聊天服务采用通用回应。检测到高风险表达时，优先进入安全回应流程，即使本轮分类失败也应可用。

## LangGraph 工作流

`backend/services/chat_service.py` 定义 `StateGraph`。情绪识别成功后，情感回应节点读取本轮情绪、当前消息和已有会话历史。识别持续失败时，图提前结束并返回兜底回复。高风险表达由聊天服务优先处理。多轮历史由 SQLite 保存。 [LangGraph 官方文档](https://docs.langchain.com/oss/python/langgraph/overview)

## 本地配置与启动

从项目根目录复制 `.env.example` 为 `.env`，填入小米 MiMo API 密钥。真实密钥仅保存在本地 `.env` 中。

```ini
MIMO_API_KEY=your_mimo_api_key
MIMO_BASE_URL=https://api.xiaomimimo.com/v1

EMOTION_MODEL=mimo-v2.6-flash
RESPONSE_MODEL=mimo-v2.6-flash

DATABASE_PATH=./data/chat.db
AGENT_SESSION_DB=./data/agent_sessions.db
```

`DATABASE_PATH` 指向聊天消息数据库，`AGENT_SESSION_DB` 为预留配置。配置中的相对路径以项目根目录为基准。

在项目根目录激活 `ai_learning` 环境、安装依赖并启动 FastAPI。

```shell
conda activate ai_learning
python -m pip install -r requirements.txt
python -m uvicorn backend.main:app --env-file .env
```

在另一个终端从项目根目录启动 Streamlit。

```shell
conda activate ai_learning
python -m streamlit run frontend/app.py
```

打开 [本地聊天界面](http://localhost:8501)。前端默认连接 `http://127.0.0.1:8000`，可通过启动前端的进程环境变量 `CHAT_API_BASE_URL` 指定其他后端地址。

刷新当前聊天地址时，前端会按地址中的会话 ID 加载 SQLite 历史。也可以在侧栏粘贴会话 ID 并点击「恢复会话」。打开「调试模式」后，新发送的消息会显示两个 Agent 的执行状态和可用的情绪结果。

## 多轮聊天接口

服务启动后，可打开 [本地 API 文档](http://127.0.0.1:8000/docs) 试用 `POST /chat`。

```json
{
  "message": "我明天要答辩，有点紧张。",
  "debug": true
}
```

首次请求省略 `session_id`。保存返回的 `session_id` 并在后续请求中传回，接口会从 SQLite 加载该会话的历史。服务重启后，使用同一个数据库文件和 `session_id` 即可续聊。

接口在本轮消息写入后一次性返回 `session_id` 和 `reply`。开启 `debug` 时会附带两个 Agent 的执行状态及可用的情绪识别结果。SQLite 读写失败时返回 HTTP 503。

`GET /history` 接受 `session_id` 查询参数，按时间顺序返回该会话的 `role` 和 `content`。会话暂无消息时返回空列表。

## V1.0 验收清单

- [ ] 两个 Agent 分别使用 `mimo-v2.6-flash` 成功调用真实 MiMo API。
- [ ] Agent 1 可识别正面、负面和中性情绪，并返回通过 Pydantic 校验的细分情绪标签。
- [ ] Agent 2 结合情绪结果和历史上下文，生成符合场景的鼓励、安慰或自然回应。
- [ ] 同一会话支持多轮聊天，每轮重新执行情绪识别。
- [ ] 用户消息和 Agent 回复持久化到 SQLite，程序重启后可恢复原对话。
- [ ] Streamlit 默认只显示聊天，开启调试模式后显示 Agent 执行状态和本轮情绪结果。
- [ ] FastAPI 以非流式方式一次性返回完整回复。
- [ ] 测试覆盖模型 API 错误、结构化输出不合法和高风险情绪表达三种场景。

开发顺序为 MiMo 模型接入与实测 → 双 Agent 实现 → FastAPI 编排 → SQLite 持久化 → Streamlit 界面 → 系统测试。
