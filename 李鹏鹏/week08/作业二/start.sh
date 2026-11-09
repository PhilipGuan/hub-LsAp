#!/usr/bin/env bash
# 一键启动：后端 :8000 + 前端 :3000（Git Bash / WSL / macOS / Linux）
set -e
cd "$(dirname "$0")"

if [ ! -f .env ]; then
  echo "[start] 未找到 .env，请先：cp .env.example .env 并填写密钥"
  exit 1
fi

if ! python -c "import fastapi" 2>/dev/null; then
  echo "[start] 安装后端依赖..."
  python -m pip install -r backend/requirements.txt
fi

echo "[start] 启动后端 :8000 ..."
python -m uvicorn backend.app:app --port 8000 &
BACK_PID=$!

cd frontend
if [ ! -d node_modules ]; then
  echo "[start] 安装前端依赖..."
  npm install
fi

echo "[start] 启动前端 :3000 ..."
npm run dev &
FRONT_PID=$!

cd ..
trap 'kill $BACK_PID $FRONT_PID 2>/dev/null' EXIT
echo "[start] 后端 http://localhost:8000 ｜ 前端 http://localhost:3000 （Ctrl+C 退出）"
wait
