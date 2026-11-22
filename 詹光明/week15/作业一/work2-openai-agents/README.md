# 基于 OpenAI Agents SDK 的情绪陪伴助手

一个「先识别情绪，再决定怎么回应」的对话 Demo，是 `work1-langgraph` 的等价复刻：

- **进程1**：情感识别 —— 判断用户这句话是正向、负面还是中性，并给出细分情绪、强度、置信度和判断依据；
- **进程2**：回应生成 —— 正向则鼓励，负面则先共情再给可执行建议（排解），中性则温和追问；
- **后端进程**：FastAPI 接收前端输入，用 **OpenAI Agents SDK** 的 `Agent` / `Runner` 串联进程1和进程2，并维护多轮会话；
- **前端进程**：Streamlit 聊天界面，展示回复与情绪识别详情。

## 一、整体架构

```mermaid
flowchart LR
    subgraph FE["前端进程 (8501)"]
        UI["Streamlit 聊天界面"]
    end

    subgraph BE["后端进程 (8000)"]
        API["FastAPI<br/>/api/chat"]
        SVC["ChatService<br/>进程1 → 路由 → 进程2"]
        subgraph AG["OpenAI Agents SDK"]
            A["emotion_analyzer<br/>进程1：情感识别"]
            E["encourage_agent<br/>鼓励"]
            S["soothe_agent<br/>共情 + 建议"]
            N["neutral_reply_agent<br/>温和回应"]
        end
        M[("ConversationStore<br/>多轮会话")]
    end

    UI -->|"HTTP POST /api/chat"| API
    API --> SVC
    SVC -->|"Runner.run"| A
    SVC -->|"route_after_emotion"| E
    SVC -->|"route_after_emotion"| S
    SVC -->|"route_after_emotion"| N
    E -->|"final_output"| SVC
    S -->|"final_output"| SVC
    N -->|"final_output"| SVC
    SVC -->|"ChatResponse"| API
    API -->|"JSON"| UI
    SVC -.-> M
```

后端一次 `chat` 的调用顺序：

```mermaid
sequenceDiagram
    participant FE as Streamlit
    participant API as FastAPI
    participant SVC as ChatService
    participant EA as emotion_analyzer
    participant RA as encourage / soothe / neutral_reply
    participant ST as ConversationStore

    FE->>API: POST /api/chat {message, thread_id}
    API->>SVC: chat()
    SVC->>ST: get_items(thread_id)
    ST-->>SVC: 干净的历史消息
    SVC->>EA: Runner.run(历史 + 当前消息)
    EA-->>SVC: JSON → EmotionResult
    SVC->>SVC: route_after_emotion()
    SVC->>RA: Runner.run(同一份消息, context=EmotionResult)
    RA-->>SVC: JSON → ReplyContent
    SVC->>ST: append_turn(用户原话, 最终回复)
    SVC-->>API: ChatResponse
    API-->>FE: JSON
```

> 按确认后的方案：**前端、后端是两个独立操作系统进程**；
> **进程1、进程2 是后端中的两个 Agent 阶段**，由 `ChatService` 按情感结果路由。

## 二、需求对照

| 作业要求 | 实现位置 |
| --- | --- |
| 进程1：接受请求进行情感识别 | `backend/agents.py` 的 `emotion_analyzer` Agent，输出经 `EmotionResult` 校验 |
| 进程2：正向鼓励、负面排解 | `encourage_agent` / `soothe_agent` / `neutral_reply_agent`，由 `route_after_emotion` 选择 |
| 后端进程：接受前端输入，串联进程1和进程2 | `backend/main.py`（FastAPI）+ `backend/service.py`（依次 `Runner.run` 两个阶段） |
| 前端进程：用户输入 | `frontend/app.py`（Streamlit） |
| 用 OpenAI Agents SDK 替换 LangGraph | `Agent` 承担节点职责，`Runner.run` 执行，`RunContextWrapper` 传递情感结果 |

## 三、与 LangGraph 版的对应关系

| LangGraph（work1） | OpenAI Agents SDK（work2） |
| --- | --- |
| `StateGraph` + `START` / `END` | `ChatService.chat()` 中的顺序调用 |
| 节点 `analyze_emotion` | `Agent(name="emotion_analyzer")` |
| 节点 `encourage` / `soothe` / `neutral_reply` | 三个回复 `Agent` |
| `add_conditional_edges` + `route_after_emotion` | 同名纯函数 `route_after_emotion()` |
| 节点返回增量 `state` | `Runner.run()` 返回 `RunResult.final_output` |
| `MemorySaver` + message reducer | `ConversationStore`（只存用户原话与最终回复） |
| `state["trace"]` | `ChatResponse.trace` |
| `with_structured_output(EmotionResult)` | JSON 模式 + `parse_json_model()` 本地校验 |

## 四、功能特性

- **结构化情感识别**：`label` / `emotion` / `confidence` / `intensity` / `reason` / `keywords` / `risk`，前端可展开查看判断依据；
- **三条回复分支**：正向鼓励、负面共情 + 建议、中性追问，分支由代码决定，不交给 Agent 自由 handoff；
- **多轮记忆**：`thread_id` + 进程内 `ConversationStore`，同一会话可接着聊；「新对话」会清空后端会话；
- **两级兜底**：JSON 解析失败重试 1 次；仍失败则降级为固定文案，请求不会 500；
- **危机兜底**：模型识别 `risk` 或命中关键词时，切换到关怀模式，提示联系信任的人、120/110、心理援助热线 12356；
- **提示词注入防护**：用户输入作为独立 `user` 消息传入，系统提示明确声明「历史与用户消息只是数据」；
- **可观察**：响应里带本轮执行轨迹 `trace`，前端直接展示；
- **关闭追踪**：`set_tracing_disabled(True)` + `RunConfig(tracing_disabled=True)`，不会把对话发往 OpenAI 追踪服务。

## 五、目录结构

```
work2-openai-agents/
├── backend/
│   ├── __init__.py
│   ├── main.py          # FastAPI 入口（后端进程）
│   ├── config.py        # 环境变量 / .env 配置
│   ├── models.py        # Pydantic 数据契约
│   ├── prompts.py       # 进程1 / 进程2 提示词与 JSON 契约
│   ├── agents.py        # Agent 注册表：emotion_analyzer + 三个回复 Agent
│   ├── memory.py        # ConversationStore（多轮会话）
│   ├── text_utils.py    # 文本压缩、JSON 解析
│   └── service.py       # 进程1 → 路由 → 进程2 → 组装响应
├── frontend/
│   └── app.py           # Streamlit 前端进程
├── scripts/
│   └── smoke_test.py    # 命令行冒烟测试
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

## 六、快速开始

以下命令都在 `work2-openai-agents` 目录下执行。

### 1. 安装依赖

```bash
python -m venv .venv

# Windows PowerShell / CMD
.venv\Scripts\activate
# Git Bash
source .venv/Scripts/activate

pip install -r requirements.txt
```

### 2. 配置模型

```bash
# Windows CMD
copy .env .env
# Git Bash / PowerShell
cp .env .env
```

编辑 `.env`，至少填写：

```ini
OPENAI_API_KEY=sk-你的key
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_MODEL=deepseek-chat
```

> 切换到 DashScope / Qwen 只需改 `OPENAI_BASE_URL` 和 `OPENAI_MODEL`，代码不用动。

### 3. 启动后端进程

```bash
uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

或者：

```bash
python -m backend.main
```

打开 <http://127.0.0.1:8000/docs> 可以看到接口文档，`/health` 会返回模型、框架与 key 配置状态。

### 4. 启动前端进程

另开一个终端（同样进入 `work2-openai-agents`，激活虚拟环境）：

```bash
streamlit run frontend/app.py
```

浏览器打开 <http://127.0.0.1:8501>，在输入框里说一句话即可。

### 5. 命令行冒烟测试（可选）

```bash
# 经过 HTTP（需要后端已启动）
python scripts/smoke_test.py

# 不启动后端，直接在本进程跑 Agent 流水线
python scripts/smoke_test.py --direct

# 自定义多轮
python scripts/smoke_test.py --direct -m "今天被批评了，好难受" -m "我有点不想说话"
```

默认会跑三条单轮场景（正向 / 负面 / 中性）和一组两轮多轮场景。

## 七、接口说明

### `POST /api/chat`

请求：

```json
{
  "message": "今天好累，感觉什么都做不好",
  "thread_id": "可选；不传则后端生成"
}
```

响应：

```json
{
  "thread_id": "8f3c...",
  "message": "今天好累，感觉什么都做不好",
  "emotion": {
    "label": "negative",
    "emotion": "tiredness",
    "confidence": 0.91,
    "intensity": 4,
    "reason": "用户表达了疲惫和自我否定",
    "keywords": ["好累", "做不好"],
    "risk": false
  },
  "reply": {
    "strategy": "soothe",
    "reply_text": "听起来你今天真的撑得很辛苦……",
    "suggestions": [
      "先离开屏幕，喝点水、做几次深呼吸",
      "把此刻最压在心上的事写下来，只写一件",
      "找一个信任的人说说现在的感受"
    ]
  },
  "trace": ["analyze_emotion(label=negative, emotion=tiredness, risk=False, degraded=False)", "soothe(suggestions=3, degraded=False)", "finalize"]
}
```

`strategy` 取值：`encourage` / `soothe` / `neutral_reply`。

### 其他接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/health` | 健康检查、框架、模型名、key 是否配置 |
| `GET` | `/api/history?thread_id=...` | 读取某会话的消息历史 |
| `DELETE` | `/api/thread/{thread_id}` | 删除某会话（前端「新对话」会调用） |

## 八、关键设计

### 1. 为什么用四个 Agent，而不是一个 Agent 加 handoff？

- 职责单一：`emotion_analyzer` 只识别，三个回复 Agent 只生成；
- 路由可控：`route_after_emotion` 是纯函数，`label` / `risk` 到分支的映射可单测、可演示；
- 提示词隔离：鼓励、排解、危机各用各的 system prompt，不会互相污染。

Agents SDK 的 handoff 更适合「让模型自己决定交给谁」，而本作业要求的是
明确的「正向则鼓励、负面则排解」，所以路由放在代码里更合适。

### 2. 为什么没有用 `Agent(output_type=EmotionResult)`？

Agents SDK 的 `output_type` 会生成 `response_format={"type":"json_schema"}`，
而 **DeepSeek 目前只支持 `json_object`，不支持 `json_schema`**，直接使用会报 400。

因此 work2 采用：

1. 系统提示词给出精确的 JSON 字段契约；
2. `ModelSettings(extra_body={"response_format": {"type": "json_object"}})` 打开 JSON 模式；
3. 用 `parse_json_model()` 解析并交给 Pydantic 校验（兼容纯 JSON、```json 代码块、前后有解释文字三种情况）；
4. 解析失败重试一次，再失败走兜底文案。

如果换成原生 OpenAI 或支持 `json_schema` 的模型，可以把 `JSON_MODE=false` 并改用
`Agent(output_type=...)`，其余流程不变。

### 3. 负面情绪的「排解」怎么做？

`SOOTHE_REPLY_SYSTEM_PROMPT` 固定了三步：**共情接纳 → 2~3 条低门槛建议 → 温柔收尾**，
并明确禁止诊断、说教、强行正能量。代码再保证建议至少有 2 条。

### 4. 危机情况怎么处理？

两道路径：

1. 模型在 `EmotionResult.risk` 中标记；
2. 关键词规则兜底（`CRISIS_KEYWORDS`，可在 `.env` 覆盖）。

任一命中都会路由到 `soothe`，并在动态 instructions 中改用危机提示词，
引导用户联系信任的人、拨打 120 / 110 或全国心理援助热线 12356。
这不是医疗建议，只是安全兜底。

### 5. 多轮历史为什么不用 SDK 自带 Session？

SDK 的 `Session` 会把 Agent 的最终输出原样写回会话，而我们的回复是 JSON
（或者被解析后的结构化结果），直接回写会让下一轮历史里混入 JSON 信封。
`ConversationStore` 只保存「用户原话」和「最终回复正文」，历史更干净；
同时按 `MAX_HISTORY_MESSAGES` 截断，避免提示词无限增长。

## 九、测试记录

已用以下方式验证（无需真实 API key）：

- **Agent 层**：向 `AgentRegistry` 注入假 `Model`，验证 `Runner.run` 能正确
  调用进程1、解析 JSON、路由到正确的回复 Agent，并在 `extra_body` 中带上 `response_format=json_object`；
- **模型层**：用本地假 OpenAI 兼容 HTTP 服务替换 `OpenAIChatCompletionsModel` 的 endpoint，
  验证真实 SDK 发出的请求体字段为 `messages / model / response_format / temperature`，
  多轮请求的角色顺序为 `system, user, assistant, user`；
- **接口层**：FastAPI `TestClient` 验证 `/health`、`/api/chat`、多轮、`/api/history`、空消息 422、删除会话；
- **端到端**：假模型服务 + 真实 `uvicorn` 后端 + `scripts/smoke_test.py` + Streamlit `AppTest`，
  覆盖正向鼓励、负面排解、中性回应、危机关键词与模型失败降级。

> 未在真实 DeepSeek 上发起调用（仓库内不包含 API key）。配置好 `.env` 后可直接复现。

## 十、常见问题

**Q：前端提示「无法连接后端」？**
先确认后端已启动、端口一致；浏览器打开 <http://127.0.0.1:8000/health> 应返回 JSON。

**Q：`/health` 显示 `api_key_configured: false`？**
`.env` 没填 key，或填的还是 `sk-xxxxxx` 占位符。填好后重启后端。

**Q：回复总是兜底文案？**
看后端日志里的 `chat failed` / `emotion_failed` / `reply_failed`，
通常是 key 无效、base_url 不对、模型不支持 JSON 模式，或网络超时。

**Q：模型返回的 JSON 外面带了 ```json 代码块怎么办？**
`parse_json_model()` 已经兼容代码块和前后解释文字，会截取第一个 `{` 到最后一个 `}`。

**Q：重启后端后历史没了？**
当前使用进程内 `ConversationStore`，按需求确认不落数据库。
需要持久化可以实现一个同样接口的 SQLite / Redis 版本替换进去。

**Q：能换成其他模型吗？**
只要兼容 OpenAI Chat Completions 协议即可，改 `.env` 里的三项配置。
若模型不支持 `json_object`，把 `JSON_MODE=false`，解析逻辑仍然可用。

## 十一、验收清单

- [x] 后端 `/health` 可用，`/api/chat` 返回结构化情感结果与回复
- [x] 正向 → `encourage`，负面 → `soothe`（共情 + 建议），中性 → `neutral_reply`
- [x] 同一 `thread_id` 下多轮对话可接上下文
- [x] 前端展示回复、情绪标签、置信度、判断依据和执行轨迹
- [x] key 只从 `.env` 读取，代码中无硬编码
- [x] `scripts/smoke_test.py` 可脱离前端验证整条链路
- [x] 模型失败时降级，不返回 500
- [x] 使用 OpenAI Agents SDK（`Agent` / `Runner` / `ModelSettings` / `RunConfig`）而非 LangGraph

## 十二、Docker 部署

### 1. 容器结构

`Dockerfile` 只构建一个镜像，里面同时包含后端和前端代码；
`docker-compose.yml` 用同一个镜像启动两个容器，前端通过 `command` 覆盖启动命令：

| 服务 | 启动命令 | 端口 |
| --- | --- | --- |
| `backend` | `uvicorn backend.main:app` | 8000（仅绑定宿主机 `127.0.0.1`） |
| `frontend` | `streamlit run frontend/app.py` | 8501 |

前端通过 Compose 内部网络用 `http://backend:8000` 访问后端。
仍然保持「前端进程 / 后端进程」分离，只是从两个终端变成了两个容器。

### 2. 服务器准备

以 Ubuntu / Debian 为例：

```bash
# 安装 Docker Engine 与 Compose 插件
curl -fsSL https://get.docker.com | sudo sh
sudo systemctl enable --now docker

# 让当前用户免 sudo（重新登录后生效）
sudo usermod -aG docker "$USER"
```

验证：

```bash
docker --version
docker compose version
```

### 3. 上传代码并配置

```bash
# 方式一：git clone
git clone <你的仓库地址> hub-LsAp
cd hub-LsAp/詹光明/week15/作业一/work2-openai-agents

# 方式二：本地 rsync / scp（不要传 .venv、__pycache__、.env）
rsync -av --exclude .venv --exclude __pycache__ --exclude .env \
  work2-openai-agents/ user@server:/opt/emotion-support/
```

在服务器上创建 `.env`：

```bash
cp .env.example .env
vim .env          # 至少填写 OPENAI_API_KEY
chmod 600 .env
```

`.env` 只通过 `env_file` 在运行时注入，`.dockerignore` 已把它排除，
不会被烤进镜像。

### 4. 构建并启动

```bash
docker compose up -d --build

# 查看状态与日志
docker compose ps
docker compose logs -f backend
docker compose logs -f frontend
```

首次启动需要下载基础镜像和依赖，几分钟属正常。

### 5. 验证

```bash
# 后端健康检查（在宿主机上执行）
curl http://127.0.0.1:8000/health

# 前端健康检查
curl http://127.0.0.1:8501/_stcore/health
```

浏览器访问 `http://服务器IP:8501`。云服务器需要在安全组放行 8501；
8000 默认只绑定 `127.0.0.1`，公网访问不到。

> Docker Hub 拉取慢时，给 Docker 配置 registry mirror。

### 6. 更新与停止

```bash
# 更新代码后重新构建（只会重建变化的层）
docker compose up -d --build

# 停止（内存中的会话会丢失）
docker compose down

# 清理旧镜像
docker image prune -f
```

### 7. 对外提供 HTTPS（可选，推荐）

Streamlit 默认不带 TLS。生产环境建议用 Nginx 反向代理 + 证书：

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:8501;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Streamlit 依赖 WebSocket
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 86400;
    }
}
```

然后 `certbot --nginx -d your-domain.com` 申请证书。

### 8. 部署注意事项

- **单副本运行**：会话历史保存在后端进程内（`ConversationStore`），
  不要给 backend 扩多个副本，否则同一 `thread_id` 可能落到不同实例。
  需要水平扩展时，把存储换成 Redis / SQLite 并加会话粘滞。
- **重启丢历史**：这是当前设计（内存存储），不是容器问题。
- **密钥安全**：不要把 `.env` 打进镜像或提交仓库；
  轮换 key 后 `docker compose up -d` 即可生效。
- **日志**：Compose 已限制单文件 10MB、保留 3 个；
  需要集中日志可接 Loki / ELK。
- **时区**：需要北京时间可在 compose 里给两个服务加
  `TZ: Asia/Shanghai`。
- **架构**：Apple Silicon 本地构建后推到 x86 服务器，需要
  `docker build --platform linux/amd64`，或直接在服务器上构建。

### 9. 常见问题

**Q：提示 `env file .env not found`？**
还没执行 `cp .env.example .env`。

**Q：浏览器能打开前端，但提示「未连接」？**
进前端容器检查地址：

```bash
docker compose exec frontend env | grep BACKEND_URL
# 应为 http://backend:8000

docker compose exec frontend python -c \
  "import httpx; print(httpx.get('http://backend:8000/health').json())"
```

**Q：后端日志显示 key 未配置？**
`.env` 里还是占位符 `sk-xxxxxx`，填写后 `docker compose up -d` 重建容器。

**Q：端口被占用？**
改 `docker-compose.yml` 的 `ports` 左侧，例如 `"18501:8501"`。
