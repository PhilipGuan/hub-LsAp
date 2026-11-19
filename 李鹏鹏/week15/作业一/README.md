# 作业一:情感陪伴聊天系统(LangGraph / OpenAI Agents 双实现)

用户输入一句话 → 系统识别情感 → **正向则鼓励,负向则排解**。
分别用 **LangGraph** 和 **openai-agents** 两套框架实现完整逻辑,前后端不分离。

---

## 1. 需求原文

- 进程1:接受请求进行情感识别
- 进程2:得到识别结果后,正向则鼓励,负面则排解
- 后端进程:接受前端输入,串联进程1和进程2
- 前端进程:用户输入

要求:① 两套代码;② 先意图澄清并生成 README.md;③ 前后端不分离,前端用最新版 layui(https://layui.dev/),后端 FastAPI + SQLModel + Kafka,以 uvicorn 方式启动。

## 2. 意图澄清记录(已与需求方确认)

| # | 澄清问题 | 确认结论 |
|---|---------|---------|
| 1 | LLM 用哪家接口? | OpenAI 兼容接口,`.env` 配置 `OPENAI_BASE_URL / OPENAI_API_KEY / OPENAI_MODEL`,兼容 DashScope、DeepSeek 等,key 由使用者填写 |
| 2 | "进程1/进程2"是真实进程还是逻辑节点? | 先按 **真实独立进程** 实现(通过 Kafka 话题衔接);后按要求合并为 **同一进程内两个 Kafka 消费线程**,只需 `uvicorn` 启动(详见第 9 节) |
| 3 | 情感分类粒度? | 二分类 positive/negative,**中性归入正向**(→鼓励) |
| 4 | 数据库? | SQLModel + **SQLite** 文件库(`app.db`),记录每次输入、情感标签与回复 |
| 5 | layui 引入方式? | 内置最新 2.x dist(`layui@2`,2.11.4)到 `static/layui/`,**离线可用** |
| 6 | 两套应用端口? | LangGraph 版 **8000**,OpenAI Agents 版 **8001**,可同时运行 |
| 7 | 容错策略? | Kafka/LLM 出错时错误信息直接回显前端,不做降级兜底(作业场景) |

另:题目中 "unicorn" 按 **uvicorn** 理解。

## 3. 总体架构(两套应用拓扑相同)

```
浏览器(layui 页面,由 FastAPI 直接托管)
   │  POST /api/chat {text}          GET /api/chat/{id} 轮询结果
   ▼
单个 uvicorn 进程:main.py (FastAPI) + 3 个 Kafka 消费线程
   │  ① 消息落库(SQLModel/SQLite, status=pending)
   │  ② 生产消息 ──────────────► Kafka 话题 <前缀>_requests
   ▼
线程 w1:worker1.handle(情感识别,LLM 输出 positive/negative)
   │  生产 {id,text,label} ────► Kafka 话题 <前缀>_sentiment
   ▼
线程 w2:worker2.handle(正向→鼓励 / 负向→排解,LLM 生成中文回复)
   │  生产 {id,label,reply} ───► Kafka 话题 <前缀>_replies
   ▼
线程 backend:apply_reply → 更新数据库(status=done/error) → 前端轮询取回展示
```

- 进程1/进程2 是同一 uvicorn 进程里的两个消费线程(逻辑仍经 Kafka 话题接力),启动一条 `uvicorn` 命令即全链路就绪。
- 话题前缀:LangGraph 版 `lg_`,OpenAI Agents 版 `oa_`,两套共用一个 Kafka 互不干扰。
- 任一环节失败时,错误消息直接发往 `<前缀>_replies`,前端能看到具体错误。
- 消费线程由 `common.consume_loop()` 统一负责,异常 3 秒后自动重试;消费者组持久化偏移 + `auto_offset_reset=earliest`:先发消息后启动应用也不会丢。

## 4. 目录结构

```
作业一/
├── README.md                  # 本文件(意图澄清 + 使用文档)
├── .gitignore                 # 忽略 .env / app.db / __pycache__ 等
├── ruff.toml / pyrightconfig.json  # 本机代码检查配置(不影响运行)
├── langgraph_app/             # 实现一:LangGraph 版(端口 8000)
│   ├── main.py                # 唯一入口:FastAPI + SQLModel + 启动 3 个 Kafka 消费线程
│   ├── worker1.py             # 进程1 逻辑:LangGraph 单节点图做情感识别(handle)
│   ├── worker2.py             # 进程2 逻辑:LangGraph 条件边路由 鼓励/排解 节点(handle)
│   ├── common.py              # 环境变量、Kafka 连接、话题定义、消费循环
│   ├── selfcheck.py           # 离线自检(打桩 LLM/Kafka,无需真实环境)
│   ├── Dockerfile             # 镜像构建(暴露 8000)
│   ├── .dockerignore          # 构建上下文排除 .env / app.db
│   ├── static/index.html      # 前端:layui 聊天页面(前后端不分离)
│   ├── static/layui/          # 内置 layui 2.11 dist(CSS/JS/字体,离线可用)
│   ├── .env.example           # 配置模板,复制为 .env 后填写
│   └── requirements.txt
└── openai_agents_app/         # 实现二:openai-agents 版(端口 8001)
    ├── main.py / common.py / selfcheck.py / static/ / .env.example  # 与上面同构
    ├── Dockerfile             # 镜像构建(暴露 8001)
    ├── worker1.py             # 进程1:sentiment Agent + Runner.handle
    └── worker2.py             # 进程2:encourager / comforter 两个 Agent,按标签选择
```

## 5. 快速开始

### 5.1 环境准备

- Python 3.12(本机为 conda 环境 `py312`,依赖已全局安装好)
- Kafka broker:**172.30.0.4:9094**(实验环境,需先启动)
  ⚠ 必须用 **9094** 外部端口:9092 的 broker 通告地址是 `localhost:9092`,外部客户端会超时。
- 一个 OpenAI 兼容的 LLM 接口(base_url / api_key / model)

### 5.2 安装依赖(如已安装可跳过)

```powershell
python -m pip install -r langgraph_app/requirements.txt -r openai_agents_app/requirements.txt
```

### 5.3 配置 LLM

两套应用各自目录下:

```powershell
Copy-Item .env.example .env   # 然后编辑 .env,填入 OPENAI_BASE_URL / OPENAI_API_KEY / OPENAI_MODEL
```

### 5.4 离线自检(不依赖 Kafka/LLM,验证核心逻辑)

```powershell
cd langgraph_app;     python selfcheck.py   # 输出:langgraph 版自检通过 (OK)
cd ..\openai_agents_app; python selfcheck.py   # 输出:openai-agents 版自检通过 (OK)
```

### 5.5 启动(单条 uvicorn 命令,无需额外启动 worker)

LangGraph 版(端口 8000):

```powershell
cd langgraph_app
uvicorn main:app --host 0.0.0.0 --port 8000
```

OpenAI Agents 版(端口 8001):

```powershell
cd openai_agents_app
uvicorn main:app --host 0.0.0.0 --port 8001
```

启动日志会打印三行 `已启动 [w1]/[w2]/[backend] 消费线程`,之后即为全链路就绪;Ctrl+C 停止全部。

浏览器访问 http://localhost:8000 (或 8001),输入心情文本,Ctrl+Enter 发送。

### 5.6 Docker 运行(可选)

两个应用各自带 `Dockerfile`,镜像内只装运行所需文件,`.env`/`app.db` 不进镜像。

```powershell
# LangGraph 版 -> http://localhost:8000
docker build -t sentiment-lg langgraph_app
docker run --rm -p 8000:8000 --env-file langgraph_app/.env sentiment-lg

# OpenAI Agents 版 -> http://localhost:8001
docker build -t sentiment-oa openai_agents_app
docker run --rm -p 8001:8001 --env-file openai_agents_app/.env sentiment-oa
```

注意:

- `--env-file` 里必须有 `OPENAI_BASE_URL / OPENAI_API_KEY / OPENAI_MODEL / KAFKA_BOOTSTRAP`;容器内的 `localhost` 指容器自己,所以 `KAFKA_BOOTSTRAP` 要写 IP(如实验环境 `172.30.0.4:9094`)。
- SQLite 库落在容器内的 `/app/app.db`(`docker rm` 后历史记录丢失);要保留就 `docker cp <容器名>:/app/app.db ./app.db` 导出。
- 镜像基于 `python:3.12-slim`,依赖全部有 manylinux wheel,不需要 gcc。

### 5.7 验证用例

| 输入 | 预期标签 | 预期行为 |
|------|---------|---------|
| 今天升职了,太开心了! | positive(绿色徽标) | 进程2 走鼓励分支,热情鼓励 |
| 最近压力好大,感觉什么都做不好 | negative(橙色徽标) | 进程2 走排解分支,共情安抚 |
| 今天星期三 | positive(中性归正向) | 鼓励分支 |

启动后的 uvicorn 终端会打印每条消息的处理日志,可直观看到"进程1 → 进程2"的接力。

## 6. 两套实现的核心差异

| | LangGraph 版 | openai-agents 版 |
|---|---|---|
| 进程1 | `StateGraph` 单节点图:`START → classify → END` | `Agent(name="sentiment")` + `Runner.run_sync` |
| 进程2 | **条件边**按标签路由:`START →(positive)→ encourage / (negative)→ comfort → END` | 两个 Agent(encourager/comforter),`pick_agent(label)` 选择后 `Runner.run_sync` |
| LLM 调用 | 节点内直接用 `openai.OpenAI` 客户端 | SDK 的 `OpenAIChatCompletionsModel` 挂自定义 `AsyncOpenAI`,并 `set_tracing_disabled(True)`(第三方接口无官方 tracing) |
| 事件循环 | 同步(无需事件循环) | 每条消息走 `Runner.run_sync()`,它复用该消费线程的默认事件循环,AsyncOpenAI 连接池因此不会跨事件循环复用 |

LangGraph 版进程2 的路由核心:

```python
builder.add_conditional_edges(START, lambda s: s["label"],
                              {"positive": "encourage", "negative": "comfort"})
```

openai-agents 版进程2 的选择核心:

```python
encourager = Agent(name="encourager", instructions=ENCOURAGE_SYS, model=model)
comforter  = Agent(name="comforter",  instructions=COMFORT_SYS,  model=model)
result = Runner.run_sync(encourager if label == "positive" else comforter, text)
```

情感识别统一约定:LLM 只输出 `positive`/`negative` 一个词;解析时只要包含 `negative` 即判负向,兼容模型偶尔输出多余文字。

## 7. API 与数据模型

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | layui 聊天页面 |
| POST | `/api/chat` |  body `{"text": "..."}`,返回 `{"id": "..."}`;空输入 400 |
| GET | `/api/chat/{id}` | 查询处理结果,不存在 404 |

`Message` 表(SQLModel):`id`(uuid 主键)、`text`、`label`、`reply`、`status`(pending/done/error)、`created_at`(UTC)。

## 8. 常见问题与已知限制

- **Kafka 连不上**:确认实验环境已启动、用 9094 端口;后端启动时 Kafka 不可用不会崩溃,消费线程每 3 秒自动重试,但发消息会报错回显。
- **TCP 通但报 Failed to update metadata**:环境重启后 broker 通告地址(advertised.listeners)可能变化,客户端会按通告地址重连。用 `KafkaAdminClient(...)._client.cluster.brokers()` 查看通告地址是否本机可达。
- **回复一直 pending**:检查启动日志里三条 `已启动 [w1]/[w2]/[backend] 消费线程` 是否都出现、`.env` 的 key/model 是否正确;前端 90 秒后提示超时。
- **kafka-python 版本**:需 ≥2.2(旧版 2.0.2 在 Python 3.12 上有 `six.moves` 兼容问题)。
- **layui 已本地内置**(`static/layui/`):页面不依赖外网 CDN,离线也能正常显示和发送;升级时替换该目录即可。若该目录缺失或 JS 未加载,发送按钮仍可工作(只是少了 layui 样式)。
- 已知限制(作业场景可接受):单分区话题、轮询而非 WebSocket、无鉴权、SQLite 单文件库、错误不降级。

## 9. 交付调整:worker 合并进 uvicorn(只需一条启动命令)

题目原本的“进程1/进程2”是三个独立 Python 进程。交付时按要求改为**同一 uvicorn 进程内三个 Kafka 消费线程**:

| 项 | 调整前 | 调整后 |
|---|---|---|
| 启动 | `python worker1.py` + `python worker2.py` + `uvicorn main:app` | 仅 `uvicorn main:app` |
| worker1.py / worker2.py | 自带 `main()` 消费循环与 `python workerX.py` 入口 | 只保留**业务逻辑** `handle(d)`,由 main.py 的线程调用 |
| 消费循环 | 三个文件各自写一份 | 统一为 `common.consume_loop(topic, group, handle)`(断线 3 秒重试) |
| Kafka 话题 | requests → sentiment → replies | **不变**,三段流水线依旧经 Kafka 接力 |
| 删除的脚本 | — | `run.bat` / `run.sh`(仅用于拉起 3 个进程,已无必要) |

效果:启动与关停只需一条命令/一个终端;日志集中在一个终端;仍保留 Kafka 解耦与话题链路,若日后要拆回独立进程,只需给 driver 写回 `python workerX.py` 入口并各自调 `common.consume_loop`。

openai-agents 版因此改为同步调用 `Runner.run_sync`(它复用当前线程的默认事件循环),每个消费线程各自一个事件循环,`AsyncOpenAI` 连接池不会跨事件循环复用。
