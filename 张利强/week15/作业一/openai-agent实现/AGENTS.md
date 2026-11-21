# AGENTS.md

## 范围

- 本目录是独立作业项目（情感支持对话系统），**只在当前目录内读写文件**；父仓库其他周次/作业目录不要读取或修改。
- `README.md` 是设计规范；进程代码在 `backend/`（`config.py`、`llm.py`、`contracts.py`、`sentiment_service.py`、`response_service.py`、`api.py`），前端在 `frontend/`。文档与代码冲突时以代码为准。
- 所有运行配置（含 Qwen Key）在项目根目录 `.env`（已被 gitignore），模板见 `.env.example`；不要在代码里硬编码 Key。
- 文档、注释、前端文案使用中文。

## Python 环境

- 统一使用 **Python 3.12**。注意 PATH 上的 `python3` 是 3.13，`python3.12` 不存在；3.12 在 conda 环境 `py312`：
  - 直接运行：`/opt/anaconda3/envs/py312/bin/python <script>.py`
  - 建项目虚拟环境：`/opt/anaconda3/envs/py312/bin/python -m venv .venv && source .venv/bin/activate`
- 依赖装进 `.venv` 或 `py312` 环境，不要装进 conda base。项目里已有 `.venv`，优先用 `.venv/bin/python`。
- 仓库没有 pytest / ruff / CI 配置；改动后至少跑 `.venv/bin/python -m compileall -q backend`。

## 运行

本地进程方式：

```bash
cp .env.example .env                    # 填 DASHSCOPE_API_KEY
docker compose up -d kafka redis        # 本地依赖
python -m backend.sentiment_service     # 进程 1（必须从项目根目录 -m 运行）
python -m backend.response_service      # 进程 2
uvicorn backend.api:app --host 0.0.0.0 --port 8000   # 必须单 worker，禁止 --workers > 1
```

Docker 方式（镜像包含三个进程，compose 一并起 Kafka/Redis）：

```bash
docker build -t support-agent:latest .
docker compose up -d --no-build       # http://localhost:8000
docker compose logs -f api
docker compose down
```

> 本机 `docker compose build` 受 Compose/Buildx 与中文路径的兼容问题影响，统一用 `docker build` + `--no-build`。

配置全部来自 `.env`（`backend/config.py` 加载）：`DASHSCOPE_API_KEY`、`QWEN_BASE_URL`、`QWEN_MODEL`、`KAFKA_BOOTSTRAP`、`REDIS_URL`、`LLM_TIMEOUT_SECONDS` 等。容器内由 compose 注入 `KAFKA_BOOTSTRAP=kafka:29092`、`REDIS_URL=redis://redis:6379/0` 和 `DOTENV_OVERRIDE=false`，不要让容器再用 `localhost`。

## 最容易写错的点

- FastAPI 必须单 worker：`stream_queues` 是进程内内存，多 worker 会导致 SSE 永远等不到。
- 进程必须用 `python -m backend.xxx` 从项目根启动，模块内导入是 `backend.*`，直接跑 `python backend/xxx.py` 会失败。
- SSE 响应头必须带 `X-Accel-Buffering: no`；不要用 `BaseHTTPMiddleware`（会缓冲响应体让 SSE 失效）。
- `Runner.run_streamed()` 必须完整消费 `stream_events()` 迭代器，提前 `break` 会破坏会话持久化。
- token 走 Redis Pub/Sub（`stream:{request_id}`），只有 `chat.request` / `sentiment.result` / `chat.response` 走 Kafka。
- Kafka 关闭自动提交，下游 `send_and_wait()` 成功后才 `commit()`；消息 key 用 `session_id` 保证同会话有序。
- `RedisSession` 全局复用同一个 redis 客户端，必须设 `ttl`；澄清回复不写入 Session。
- 情感识别进程不传 Session；进程 2 必须传，否则多轮语气不连贯。
- Qwen 走 OpenAI 兼容接口（`backend/llm.py`）；情感识别用 `json_object` + Pydantic 校验，不要改回 `output_type`（qwen-plus 不支持 `json_schema`）。
