#!/usr/bin/env bash
# 启动后端（在项目根目录运行，自动加载 .env；端口默认 8000，被占用时改 PORT=8001）
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python}"
PORT="${PORT:-8000}"
HOST="${HOST:-127.0.0.1}"

exec "$PYTHON" -m uvicorn backend.app:app --host "$HOST" --port "$PORT"