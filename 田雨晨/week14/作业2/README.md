# 多模态 RAG 问答系统（第一版最小实现）

多 PDF 上传解析 + 批次/单文件解析状态展示 + 多模态图文问答。

- 后端：FastAPI + pydantic（数据结构单独存放）+ SQLAlchemy（本地 SQLite）
- 问答智能体：固定编排 + 一次轻量决策，只读检索数据（`backend/agent/`，服务端口 8100）
- 解析工作器：MinerU 解析 → 文本切片 + 图片资源 → 向量入库（`parse-worker/`）
- 前端：Next.js 16 + React 19 + TypeScript，只消费 HTTP 接口（`frontend/`）
- 详细设计见 [docs/README.md](docs/README.md)

## 快速开始（本地开发，不需要 Docker）

本地默认：SQLite + 本地目录对象存储 + 内存队列；没有 MinerU / 模型密钥时也能用离线 mock 跑通全链路。

### 一键启动（推荐）

```bash
python start_all.py                 # 后端(8000) + 解析工作器 + 智能体(8100) + 前端(3000)
python start_all.py --real-models   # 智能体/工作器使用真实模型（读 deploy/.env 的 VLM_*/EMBEDDING_*）
python start_all.py --no-agent      # 不起智能体服务，后端用离线假智能体
python start_all.py --no-frontend   # 只起后端 + 解析工作器 + 智能体
```

脚本会自动选装好依赖的 Python（默认 py312）、首次运行自动 `npm install`、按顺序等待各服务就绪，
然后打印访问地址；按 `Ctrl+C` 停止全部服务。日志在 `.run/logs/`，进程号在 `.run/pids.json`。

访问地址：前端 <http://localhost:3000>、接口文档 <http://127.0.0.1:8000/docs>、
后端健康 <http://127.0.0.1:8000/health>、智能体健康 <http://127.0.0.1:8100/health>。

### 手动启动（分终端）

### 0. 环境准备

```bash
# 后端依赖（Python 3.12，本机 conda 环境 py312）
D:\Anaconda\envs\py312\python.exe -m pip install -r backend/requirements.txt

# 前端依赖（Node.js 20+）
cd frontend && npm install && cd ..
```

### 1. 启动后端（端口 8000）

```bash
# 在仓库根目录执行；启动时会初始化数据库表
uvicorn backend.app.main:create_app --factory --host 127.0.0.1 --port 8000
```

- 接口文档：<http://127.0.0.1:8000/docs>，健康检查：<http://127.0.0.1:8000/health>
- 本地数据落在 `backend/data/`（SQLite 文件 + 对象存储目录，已 gitignore）
- 暂时没有智能体服务时，可用 `AGENT_STUB=1 uvicorn ...` 让后端用离线假智能体

### 2. 启动解析工作器（上传的 PDF 由它解析入库）

```bash
# 默认：离线 mock 解析 + 确定性假向量（不依赖 MinerU / 向量模型）
WORKER_STUB_MODELS=1 python parse-worker/worker.py --serve

# 接真实 MinerU：需要 MinerU HTTP 服务，工作器用真实向量模型（读 deploy/.env 的 EMBEDDING_*）
MINERU_API_URL=http://127.0.0.1:8080 python parse-worker/worker.py --serve
```

- 工作器按数据库状态机取 `queued/retrying` 文档，状态写回数据库
- 说明见 [parse-worker/README.md](parse-worker/README.md)

### 3. 启动问答智能体（端口 8100，问答功能需要）

```bash
# 真实模型：密钥读 deploy/.env 的 VLM_* / EMBEDDING_*
uvicorn backend.agent.service:create_app --factory --host 127.0.0.1 --port 8100

# 离线联调（不调用真实模型，使用假 LLM + 假向量）
AGENT_STUB_LLM=1 uvicorn backend.agent.service:create_app --factory --host 127.0.0.1 --port 8100
```

- 契约：`POST http://127.0.0.1:8100/answer`（仅后端调用，不对外暴露）
- 说明见 [backend/README.md](backend/README.md)

### 4. 启动前端（端口 3000）

```bash
cd frontend
cp .env.local.example .env.local   # 可选：默认已指向 http://127.0.0.1:8000
npm run dev
```

浏览器打开 <http://localhost:3000>：

- `/` 上传 PDF、查看批次/单文件解析状态（SSE 实时推送 + 5 秒轮询兜底、失败重试、刷新恢复）
- `/qa` 多模态问答（文本/图片提问、图文分段渲染、引用页码、回退提示）

> 前端与后端同机时后端已默认放行 `localhost:3000` / `127.0.0.1:3000`；换地址用 `CORS_ORIGINS` 配置。

## 自测

```bash
# 全部 Python 模块的 main 自检（43 个模块，自动切换到 py312）
python test/selfcheck.py -v
python test/selfcheck.py agent          # 只跑智能体
python test/selfcheck.py parse-worker   # 只跑解析工作器

# 单个模块
python -m backend.app.services.document
python parse-worker/worker.py

# 前端类型检查 + 生产构建
cd frontend && npx tsc --noEmit && npm run build
```

## 目录结构

```text
docs/                # 需求与设计（README.md 为权威规格）
backend/
├── app/             # 后端：FastAPI 接口、pydantic schemas、SQLAlchemy 表、服务、SSE
├── agent/           # 问答智能体服务：graph/(规划/合成)、tools/(检索等)
└── workflow.py      # 问答代码编排入口（固定顺序、单步失败兜底）
parse-worker/        # 解析工作器：MinerU 调用、切片、图片入库、向量、状态机
frontend/            # Next.js 前端（只消费接口）
deploy/.env(.example)  # 环境变量模板（模型密钥、数据库/对象存储/Kafka 配置）
test/                # selfcheck.py（一键跑全部 main）、模型连通性测试
```

各模块详细说明：[backend/README.md](backend/README.md)、[parse-worker/README.md](parse-worker/README.md)、[frontend/README.md](frontend/README.md)。

## 当前完成度

- ✅ 后端接口（上传 / 批次与单文件状态 / SSE / 手动重试 / 问答 / 签名图片）
- ✅ 问答智能体（固定编排、规划/合成、检索与图片工具、结构化回退）
- ✅ 解析工作器（MinerU 接入 + 离线 mock、切片与图文关联、幂等入库、自动重试/死信）
- ✅ 前端（上传与状态页、多模态问答页）
- ⬜ Docker 部署（`deploy/docker-compose.yml` 等，见 docs/README.md 的 Docker 部署一节）
- ⬜ Kafka 消费者模式（当前第一版工作器按数据库状态机轮询）
