#!/usr/bin/env bash
# 一键启动开发环境：初始化 -> API -> Worker -> 前端
#
# 前置条件（二选一）：
#   a) docker compose up -d            # Kafka / MySQL / MinIO / Qdrant（生产形态）
#   b) 本机已有 Kafka（如 localhost:9092），.env 用 SQLite + 本地存储（沙箱/开发形态）
# 依赖：backend/.venv 已 pip install -r backend/requirements.txt；frontend 已 npm install
set -e
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

# 使用 backend/.venv（存在时），否则要求已激活虚拟环境
PY="python"
if [ -x "backend/.venv/bin/python" ]; then
  PY="backend/.venv/bin/python"
  export PATH="$ROOT/backend/.venv/bin:$PATH"
fi

echo "[1/5] 初始化 Kafka Topic 与数据库表..."
"$PY" scripts/init_kafka_topics.py
"$PY" scripts/init_db.py

echo "[2/5] 检查 MinerU 本地解析引擎（PARSER_BACKEND=mineru 时需要）..."
if [ -x "backend/.venv/bin/mineru" ] && grep -q "^PARSER_BACKEND=mineru" .env 2>/dev/null; then
  if backend/.venv/bin/mineru server status 2>/dev/null | grep -q "Uptime"; then
    echo "  mineru server 已在运行"
  else
    MINERU_MODEL_SOURCE="${MINERU_MODEL_SOURCE:-modelscope}" backend/.venv/bin/mineru server start
    echo "  mineru server 已启动（引擎首次加载模型可能需要 1~2 分钟）"
  fi
fi

echo "[3/5] 启动 API (uvicorn, http://localhost:8000)..."
(cd backend && uvicorn app.main:app --port 8000) &
API_PID=$!

echo "[4/5] 启动解析 Worker (Kafka 消费者)..."
(cd backend && "$PY" -m worker.main) &
WORKER_PID=$!

echo "[5/5] 启动前端 (vite, http://localhost:5173)..."
(cd frontend && npm run dev) &
VITE_PID=$!

trap 'kill "$API_PID" "$WORKER_PID" "$VITE_PID" 2>/dev/null || true' EXIT
echo ""
echo "就绪：前端 http://localhost:5173 | API 文档 http://localhost:8000/docs | 健康检查 /api/health"
echo "按 Ctrl+C 退出全部进程"
wait
