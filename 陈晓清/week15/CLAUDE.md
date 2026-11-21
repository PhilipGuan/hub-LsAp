# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概览

第 15 周作业：**基于 Kafka 的情感支持对话 Demo**（FastAPI + Vue）。前端提交用户文本，FastAPI 生产 Kafka 消息，两个独立 worker 进程依次完成「情感识别」和「回复生成」，结果经 Kafka 回到后端并一次性返回 JSON。

同一套业务流程要求实现两版 worker，二者共享 FastAPI 后端、Vue 前端、Kafka Topic 和消息结构：

- `backend/langgraph_impl/`：LangGraph + `langchain-openai` 的 `ChatOpenAI`；
- `backend/agents_sdk_impl/`：OpenAI Agents SDK（需用 Chat Completions 兼容模型封装，qwen 端点不支持 Responses API）。

**当前进度**：README 要求的功能已全部实现。包含 LLM 接入、Kafka 桥接层（`shared/schemas.py`、`shared/kafka_config.py`、`backend/kafka_bridge.py`）、**Agents SDK 版两个 worker**（`backend/agents_sdk_impl/`）、**LangGraph 版两个 worker**（`backend/langgraph_impl/`）、**FastAPI 后端**（`backend/main.py`）和 **Vue 前端**（`frontend/`）。

已验证：两套 worker 都能端到端跑通（bridge 发 `raw_input` → 情感识别 → 回复生成 → bridge 返回）；HTTP `GET /health` 200、`POST /api/chat` 200（返回 sentiment + 中文回复）、空消息 422、无 worker 时超时 504；Vite 构建通过，且经 `localhost:5173/api` 代理成功拿到 200 回复。两套 worker 共用相同消费者组，**同一时间只能运行一套**。需求细则以 `README.md` 为唯一依据。

**明确不做**（不要顺手补上）：流式输出、多轮会话/历史记忆、中性情感、危机干预分流、单元测试/Mock、用户系统与数据库持久化。（Docker 打包已按用户要求补充，见下方命令与目录结构。）

## 技术栈与运行环境

- Python 3.12（Windows + Git Bash；依赖装在全局 conda 环境 `py312`，未使用 venv）
- FastAPI / uvicorn / aiokafka / pydantic v2
- LLM：OpenAI 兼容接口，模型 `qwen3.8-flash`
- 前端：Vue 3 + Vite（尚未创建）

## 常用命令

```bash
# 安装依赖（通常已装好）
pip install -r requirements.txt

# LLM 环境自检：最小工具 agent，直接打印消息流转
python test_llm.py

# 模块自测：直接跑模块文件末尾的 main()
python -m shared.kafka_config    # 生产并消费回一条测试消息
python -m backend.kafka_bridge   # 假 worker 端到端，打印最终 JSON
python -m backend.agents_sdk_impl.agents.sentiment_agent  # 情感识别 Agent
python -m backend.agents_sdk_impl.agents.reply_agent      # 鼓励/排解回复 Agent

# FastAPI 后端（已实现）
python -m backend.main          # 自测：lifespan 启动/关闭 bridge + GET /health
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload

# Agents SDK 版 worker（长驻进程，另开两个终端；已实现）
python -m backend.agents_sdk_impl.sentiment_worker
python -m backend.agents_sdk_impl.reply_worker

# Vue 前端（已实现）；PowerShell 5.1 不支持 &&，用分号或分两行
cd frontend
npm run dev   # http://localhost:5173

# LangGraph 版 worker（长驻进程，另开两个终端；已实现。不要与 Agents SDK 版同时运行）
python -m backend.langgraph_impl.sentiment_worker
python -m backend.langgraph_impl.reply_worker

# LangGraph 图自测
python -m backend.langgraph_impl.graphs.sentiment_graph
python -m backend.langgraph_impl.graphs.reply_graph

# Docker：构建并启动整套（Kafka + FastAPI/前端 + 两个 Agents SDK worker）
docker compose up --build    # 浏览器打开 http://localhost:8000
停止：
docker compose down
# 单镜像构建（多阶段：Node 构建前端 -> Python 运行时）
docker build -t sentiment-chat-agents .
```

`POST /api/chat` 需要 Kafka 与一对 worker 同时在跑；只做 `/health` 自测时不需要 worker。

> 本文档命令按 Git Bash 书写。Windows PowerShell 5.1 **不支持 `&&`**：改用 `;` 分隔或逐行执行（如 `cd frontend; npm run dev`）。

Windows 下用 `python` 直接运行即可；若创建 venv，激活命令是 `.venv\Scripts\activate`。中文输出乱码时在脚本开头 `sys.stdout.reconfigure(encoding="utf-8")`（`test_llm.py` 已内置）。

## 架构与数据流

```text
Vue ──POST /api/chat──> FastAPI（生产 raw_input，消费 sentiment_result 并按 request_id 聚合）
                              │ produce(key=request_id)
                              ▼
                        Topic: raw_input（1 分区）
                              │ consume（group: sentiment-worker）
                              ▼
                     进程 1：情感识别（LangGraph 或 Agents SDK）
                       调用 LLM，输出 positive / negative
                              │ produce type=sentiment
                              ▼
                  Topic: sentiment_result（1 分区）
                              │ consume（group: reply-worker，只处理 type=sentiment）
                              ▼
                     进程 2：回复生成（正向→鼓励，负向→排解）
                              │ produce type=reply
                              ▼
                  FastAPI 按 request_id 匹配后返回
```

关键约束：

- 只有两个 Topic；`sentiment_result` 用 `type` 字段区分 `sentiment` / `reply`，所有消费者必须按 `type` 过滤，避免循环处理。
- 消息 key 统一用 `request_id`，保证同一请求的顺序；`sentiment_result` 单分区时后端先收到 sentiment 再收到 reply。
- **同一时间只启动一套 worker**（LangGraph 版或 Agents SDK 版）。两套同时用相同 group id 会分摊消息；确需同时运行必须加不同 group id 前缀。
- 进程 1 必须用结构化输出/严格 JSON 解析，`sentiment` 只允许 `positive` / `negative`，非法值重试或按错误处理，不得写入 Kafka。
- 后端为每个 `request_id` 建 Future：收到 sentiment 缓存，收到 reply 完成；超时 `REQUEST_TIMEOUT_SECONDS` 返回 504，LLM/Kafka 失败返回 502。
- 支持 CORS 或走 Vite 代理 `/api → http://localhost:8000`。

## 目录结构（规划）

```text
shared/
  config.py       # 已实现：加载 .env、校验变量、get_llm() 工厂、Kafka 配置
  schemas.py      # 已实现：Pydantic 消息模型（RawInput / Sentiment / Reply）与 API 请求响应
  kafka_config.py # 已实现：aiokafka 工厂、Topic 自动创建 + 模块自测 main()
backend/
  main.py         # 已实现：FastAPI 入口、/api/chat、/health、lifespan 启停 bridge、CORS
  kafka_bridge.py # 已实现：生产 raw_input、后台消费并聚合、ask() 等待结果 + 模块自测 main()
  langgraph_impl/ # 已实现
    sentiment_worker.py   # 进程 1：单节点情感图 -> type=sentiment
    reply_worker.py       # 进程 2：单节点回复图 -> type=reply
    graphs/sentiment_graph.py  # StateGraph + with_structured_output
    graphs/reply_graph.py      # StateGraph + 按情感选提示词
  agents_sdk_impl/ # 已实现
    sentiment_worker.py   # 进程 1：raw_input -> type=sentiment
    reply_worker.py       # 进程 2：type=sentiment -> type=reply
    agents/__init__.py    # get_model()：Chat Completions 兼容模型 + 关闭 tracing
    agents/sentiment_agent.py  # output_type 结构化输出 positive/negative
    agents/reply_agent.py      # 正向鼓励 / 负向排解两个 Agent
frontend/         # 已实现：Vue 3 + Vite
  package.json / vite.config.js  # /api 代理到 localhost:8000
  index.html / src/main.js / src/App.vue  # 单组件：输入、loading、情感 + 回复展示
Dockerfile        # 多阶段：Node 构建 frontend/dist -> Python 镜像由 FastAPI 托管
docker-compose.yml # Kafka + api + 两个 Agents SDK worker，一条命令启动
.dockerignore
test_llm.py       # 已实现：最初要求的最小 LangChain agent 演示脚本（保留）
```

## 编码约定

### 数据模型统一使用 Pydantic

- 所有跨进程/跨层数据结构（Kafka 消息、API 请求/响应、LLM 结构化输出）都用 **Pydantic v2 `BaseModel`** 定义，集中在 `shared/schemas.py`；不要用裸 dict 在模块间传递，也不要为同一结构在前后端各写一份不一致的定义。
- 枚举值（`sentiment: positive|negative`、`type: sentiment|reply`）用 `Literal` 约束；时间字段统一 ISO 8601 字符串。
- LLM 输出过闸用 `with_structured_output(PydanticModel)`；校验失败要转换为明确的业务错误，不要让 `ValidationError` 穿透到 Kafka 消费者。
- 配置类保持 `shared/config.py` 的 `@dataclass(frozen=True) Settings`（只读配置不是数据契约，不需要 Pydantic）。

### 配置

- 所有密钥/端点只从 `.env` 读，禁止硬编码；`.env` 已被仓库根 `.gitignore` 忽略。
- `.env` 变量名：`OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL`、`LLM_TEMPERATURE`、`LLM_MAX_TOKENS`，以及 Kafka 相关项（见 `.env.example`）。`shared/config.py` 同时兼容 README 里的 `QWEN_MODEL` 别名。
- **模型名注意**：README 写的 `qwen3-flash` 在当前端点不存在（404），实际配置为 `qwen3.8-flash`。新增调用一律走 `get_llm()`，不要另建客户端。
- `shared/config.py` 用 `Path(__file__).resolve().parent.parent / ".env"` 显式定位，不要用无参 `load_dotenv()`（在 stdin/交互执行时会触发 `find_dotenv` 断言失败）。
- 容器运行：`docker compose` 的 `env_file` 能正常解析现有 `.env`；`docker run --env-file` 更严格，键名带空格（如 `OPENAI_API_KEY =...`）会报错，需要先规范成 `KEY=VALUE`。

### LLM 调用

- LangGraph 版业务代码通过 `shared.config.get_llm()` 获取 `ChatOpenAI`；情感识别建议 `temperature=0`，回复生成用默认 0.7。
- LangGraph 版用 `StateGraph` 单节点图：情感图 `get_llm(temperature=0).with_structured_output(SentimentResult)`，回复图 `get_llm(temperature=0.7, max_tokens=1024)`。
- Agents SDK 版通过 `backend.agents_sdk_impl.agents.get_model()` 获取 `OpenAIChatCompletionsModel`；必须显式传 `AsyncOpenAI(base_url=..., api_key=...)`，并 `set_tracing_disabled(True)`——qwen 端点不支持 Responses API 与 OpenAI 平台 tracing。
- Agents SDK 的结构化输出用 `Agent(output_type=PydanticModel)`，已实测可用（`SentimentResult` / `ReplyOutput`）。
- 模型支持 tool calling，`langchain.agents.create_agent`（底层 LangGraph）已验证可用，可作为 LangGraph 版的范式。
- 改动任一 LLM 接入路径后，重跑对应模块的 `main()` 自测（`test_llm.py`、sentiment_agent、reply_agent）。

### Kafka worker

- 两个 worker 都用 `auto_offset_reset="latest"` + `enable_auto_commit=False`，处理完一条手动 `consumer.commit()`：既不回放历史消息，也不提交未处理的消息。
- worker 只处理自己负责的 `type`；`type` 不符或消息非法时记日志后 commit，绝不生产非法标签或空回复。
- LLM 调用失败只记日志并 commit，不写入非法消息；请求最终由后端按超时/502 处理，避免毒消息阻塞分区。

### FastAPI

- `backend/main.py` 的 lifespan 负责 `await bridge.start()` / `await bridge.stop()`；路由里不要重复启停 bridge。
- `/api/chat` 只做参数校验和异常映射：`RequestTimeoutError` → 504，`KafkaBridgeError` → 502；Pydantic 校验失败由 FastAPI 自动返回 422。
- CORS 已放行 `http://localhost:5173` 与 `http://127.0.0.1:5173`；前端也可改用 Vite 代理。
- 镜像内存在 `frontend/dist` 时，`backend/main.py` 在所有 API 路由之后把它挂载到 `/`（`StaticFiles(html=True)`），因此容器里 8000 端口同时提供前端和 API；本地没有 `dist` 时自动跳过，不影响 Vite 开发。

### 前端

- Vue 3 `<script setup>` + Vite，单文件组件；不引入 UI 库、路由或状态库，保持最小。
- 请求只写相对路径 `/api/chat`，由 Vite 代理转发到 `http://localhost:8000`；不要在前端硬编码后端地址。
- 非流式交互：提交后按钮进入 loading，拿到 JSON 后一次性渲染；错误直接展示后端 `detail`。
- 情感展示固定映射：`positive` → 正向，`negative` → 负向。

### 测试/自检代码保持最简

- **模块自测直接写在模块文件末尾**：`async def main()`（同步模块用 `def main()`）+ `if __name__ == "__main__":`，不新建 `test_*.py`。
- 运行方式统一为 `python -m shared.kafka_config`、`python -m backend.kafka_bridge` 这种「包.模块」形式；不要用 `python 路径/文件.py`，否则包内 import 会失败。
- 一个 `main()` 顺序跑完，直接 `print` 关键结果，不写断言框架、不拆多层函数、不做复杂错误包装。
- 需要验证多件事时，拆到各模块自己的 `main()`，而不是在单个 `main()` 里堆步骤。
- 自检失败就让异常自然抛出（非 0 退出），不要吞异常后打印一堆状态。
- `test_llm.py` 是最初要求的最小 agent 演示脚本，保留；新增模块一律遵循上面的模块内自测约定。

## 仓库约定

- 本仓库是多人作业仓库：只修改 `陈晓清/` 下的文件，不要动其他同学或老师的文件。
- 文件名不要包含空格或 `.` 等特殊符号（用 `-` / `_`）。
- 每周作业放在 `陈晓清/weekNN/` 下；提交信息为「提交第 N 周作业」风格。
