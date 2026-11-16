# AGENTS.md

## 范围（硬约束）

本项目**只涉及本目录 `张利强/week14/作业二`**。不要读取、修改、移动或清理仓库内任何其他目录（`week01`~`week13`、其他同学/老师目录、根 `README.md`）。所有命令、路径都相对于本目录。新建文件名不要含空格或 `.`。

## 项目

Kafka 多模态 RAG 问答：FastAPI + Vite/React + MinerU + Elasticsearch + 智谱 GLM-4V。

- 后端入口 `backend/app/main.py`；Kafka 消费入口 `backend/app/workers/parse_worker.py`
- 业务在 `backend/app/services/`（`mineru_parser` / `chunker` / `embedder` / `es_store` / `pipeline` / `llm`）
- 前端 `frontend/src/App.tsx`，`api.ts` 负责 REST 与手写 SSE 解析
- 运行数据在 `storage/`；日志在 `.runtime/logs/`

## 命令（在本目录下执行）

```bash
./scripts/start_all.sh    # 检查 ES:9200 / Kafka:9092 → 建 topic → API:8000 + worker + Vite:5173
./scripts/stop_all.sh     # 只停本项目 API/worker/前端
./scripts/start_backend.sh
./scripts/start_worker.sh
./scripts/start_frontend.sh
cd frontend && npx tsc --noEmit   # 前端唯一静态检查，仓库无测试
```

## 容易踩的坑

- **venv 没有 uvicorn 脚本**：`.venv` 由 conda `py312` + `--system-site-packages` 创建，依赖来自 conda。必须 `.venv/bin/python -m uvicorn ...`；装包用 `.venv/bin/python -m pip install` 并同步 `backend/requirements.txt`。
- **Kafka / ES 是本机外部服务**（Kafka 4.4、ES 9.5.2）。不要往仓库加安装包、docker-compose 或启停逻辑；`scripts/init_topics.sh` 只建 topic。
- **单节点 ES**：索引必须 `number_of_replicas: 0`，且集群已关闭磁盘水位检查（磁盘近满），否则索引变 `red`、查询 503。
- **MinerU 4.2 在 conda py312**（`/opt/anaconda3/envs/py312/bin/mineru`），不在 venv；只有 `--tier flash` 可用（`basic/standard` 依赖默认关闭的本地 parse-server）。
- MinerU 图片是文档库定位符 `doc:<id>/tier:flash/page:N/block:M`，需 `mineru read "<locator>" --format image -o <png>` 导出后再改写 Markdown 链接。
- **SSE 事件总线是进程内的**：API 只能单 worker，多 worker 会丢状态推送。
- `.env` 含 `ZHIPU_API_KEY`（已被 gitignore），当前 `MOCK_LLM`/`MOCK_EMBEDDING` 为 `false`，走真实 GLM + embedding。断网调试可临时改回 `true`，但不要提交改后的 `.env`。
- 免费多模态模型高峰会返回 `429 / 1305`；`services/llm.py` 已内置重试 + `LLM_FALLBACK_MODEL` 兜底，不要去掉这段逻辑。
- 已忽略：`.venv/`、`.runtime/`、`storage/`、`frontend/node_modules|dist/`、`.env`；`.gitignore` 只在本项目内。

## 验证（无自动化测试）

```bash
curl -s http://127.0.0.1:8000/api/health
curl -s -X POST http://127.0.0.1:8000/api/documents -F "files=@test.pdf"
curl -sN http://127.0.0.1:8000/api/events                      # 状态 SSE
curl -sN -X POST http://127.0.0.1:8000/api/chat -F "text=..."   # 问答 SSE
```
