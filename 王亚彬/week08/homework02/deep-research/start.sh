#!/usr/bin/env bash
# 启动深度研究助手（Git Bash / Linux / macOS）
set -e
cd "$(dirname "$0")"

if [ ! -f .env ]; then
  echo "[!] 未找到 .env，正在从 .env.example 生成，请填入你的 API key"
  cp .env.example .env
fi

PYTHON=${PYTHON:-python}
echo "[*] 启动服务： http://127.0.0.1:8000"
exec "$PYTHON" -m backend.app
