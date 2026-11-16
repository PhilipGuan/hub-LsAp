#!/usr/bin/env bash
# 启动应用进程。Kafka / Elasticsearch / MinerU 均为本机已有服务，本脚本只做连通性检查。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p .runtime/logs

ES_URL="${ES_URL:-http://127.0.0.1:9200}"
KAFKA_BOOTSTRAP="${KAFKA_BOOTSTRAP:-127.0.0.1:9092}"

if ! curl -s -m 3 "$ES_URL" >/dev/null 2>&1; then
  echo "[es] $ES_URL 不可用：请先启动本机 Elasticsearch" >&2
  exit 1
fi
echo "[es] ok ($ES_URL)"

if ! kafka-topics --bootstrap-server "$KAFKA_BOOTSTRAP" --list >/dev/null 2>&1; then
  echo "[kafka] $KAFKA_BOOTSTRAP 不可用：请先启动本机 Kafka" >&2
  exit 1
fi
"$ROOT/scripts/init_topics.sh" >/dev/null
echo "[kafka] ok ($KAFKA_BOOTSTRAP)"

MINERU_BIN="${MINERU_BIN:-/opt/anaconda3/envs/py312/bin/mineru}"
if [ -x "$MINERU_BIN" ]; then
  "$MINERU_BIN" server start >/dev/null 2>&1 || true
  echo "[mineru] service ensured"
else
  echo "[mineru] $MINERU_BIN 不存在，解析会失败" >&2
fi

if curl -s -m 2 http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
  echo "[api] already running"
else
  nohup "$ROOT/.venv/bin/python" -m uvicorn app.main:app --app-dir backend \
    --host 127.0.0.1 --port 8000 > .runtime/logs/api.log 2>&1 &
  echo $! > .runtime/api.pid
  echo "[api] started pid=$(cat .runtime/api.pid)"
fi

if [ -f .runtime/worker.pid ] && kill -0 "$(cat .runtime/worker.pid)" 2>/dev/null; then
  echo "[worker] already running"
else
  (
    cd backend
    nohup ../.venv/bin/python -m app.workers.parse_worker \
      > ../.runtime/logs/worker.log 2>&1 &
    echo $! > ../.runtime/worker.pid
  )
  echo "[worker] started pid=$(cat .runtime/worker.pid)"
fi

if [ -d frontend/node_modules ]; then
  if curl -s -m 2 http://127.0.0.1:5173 >/dev/null 2>&1; then
    echo "[web] already running on http://127.0.0.1:5173"
  else
    (
      cd frontend
      nohup npm run dev -- --host 127.0.0.1 --port 5173 \
        > ../.runtime/logs/web.log 2>&1 &
      echo $! > ../.runtime/web.pid
    )
    echo "[web] started on http://127.0.0.1:5173"
  fi
else
  echo "[web] skipped (frontend/node_modules 不存在，先运行 npm install)"
fi

echo
echo "打开 http://127.0.0.1:5173 ，接口文档 http://127.0.0.1:8000/docs"
