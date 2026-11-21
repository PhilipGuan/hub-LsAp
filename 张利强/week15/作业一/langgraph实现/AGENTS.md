# AGENTS.md

## 目录约束（硬性）
- 项目所有文件、命令、运行都在本目录内完成；**不要读取或修改**父目录、`../openai-agent实现` 或仓库其他部分。
- 缺信息（API Key、Kafka 地址、需求变更）时问用户，不要跨目录找答案。
- `README.md` 是本项目唯一需求来源；实现与它冲突时，先改代码对齐它，而不是反过来。

## 常用命令（必须从项目根目录执行）
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
./scripts/kafka_local.sh start            # 无 Docker 时本地 KRaft（数据在 .runtime/，占 9092 + 19093）
# 或 docker compose up -d                 # 有 Docker 时二选一

python -m workers.emotion_worker          # 进程1
python -m workers.reply_worker            # 进程2
uvicorn backend.main:app --port 8000      # 后端 + SSE

python -m compileall -q backend workers   # 语法检查（无测试套件）
python -c "import backend.main"           # 依赖/导入级冒烟
python -m http.server 5500 -d frontend    # 前端静态页
```
- 本机已用 Python 3.13.4 的 `.venv` 验证可导入、图可跑；readme 写 3.12，两者均可。
- 已用本机 KRaft Kafka 4.4 跑通「后端 + 两个 worker + SSE + SQLite」全链路；无 Qwen Key 时走降级路径（neutral + 兜底话术）。
- 启动顺序：Kafka → 两个 worker → 后端；worker 未启动时 `/chat` 会走情感/回复超时降级。
- 入口模块必须用 `python -m workers.xxx`（相对导入依赖项目根在 `sys.path`），不要 `python workers/xxx.py`。
- 前端 `app.js` 的 `API_BASE` 默认 `http://localhost:8000`，后端已开 CORS（仅 demo）。

## 不可破坏的契约
- **SSE 时序**：`emotion` → `token`×N → `done`（或 `error`）；事件名与字段是前端契约，见 README 第 6 节。
- **thread_id**：固定 `f"{user_id}:{conversation_id}"`，改格式会导致历史状态串扰/丢失。
- **LangGraph 只做回合级写入**：单节点 `record → END`，在回复结束后 `ainvoke` 一次；token 绝不进入图状态（避免 SQLite 写放大）。
- **Kafka 消费组必须互不重叠**：`backend-emotion-group` / `backend-reply-group` / `emotion-worker` / `reply-worker`；否则消息被重复消费。
- **请求-回复语义**：`correlation_id` + 进程内 `_pending` 注册表（每请求一个 `Pending`，内含 emotion/reply 两个队列）。仅支持单实例 FastAPI，多实例需换 Redis。
- **降级分工**：worker 失败只发布 `error` 字段；由后端降级为 `neutral` / 兜底话术，并在 `done.debug` 里标 `emotion_degraded` / `reply_degraded`。
- **跨进程消息体字段名**（`correlation_id`、`user_input`、`emotion_result`、`token`、`is_final`…）改动需同时改后端、两个 worker、前端。

## 结构速览
- `backend/main.py`：lifespan（KafkaBridge + AsyncSqliteSaver）、`POST /chat`、`GET /healthz`、SSE 生成器。
- `backend/graph.py`：`TurnState`（含扩展字段 `history`，用于拼多轮上下文）+ `record` 节点。
- `backend/kafka_bridge.py`：Producer/Consumer 封装、correlation 路由、`json_serializer`。
- `workers/*`：独立进程，只通过 Kafka 通信，不 import 后端路由。
- `backend/config.py`：`python-dotenv` 加载 `.env`，所有路径按 `PROJECT_ROOT` 解析。
- `data/checkpoints.sqlite`：运行时生成，勿提交。

## 容易踩的坑
- `SQLITE_PATH` 为相对路径时按**项目根**解析；从别的目录启动会把库写到意外位置。
- `AsyncSqliteSaver` 只能在 `lifespan` 的 `async with` 内持有，不要在请求处理里新建连接。
- 情感 worker 内部 `stream=True` 只是过程流式，对外只发一次完整 JSON；前端不处理部分字段。
- 情感识别失败不要抛到后端崩溃——worker 捕获后发 `error`，后端降级。
- demo 无鉴权、无限流、无危机内容处理；不要描述为可公网部署。
