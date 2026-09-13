#!/usr/bin/env bash
# -*- coding: utf-8 -*-
"""一键启动（CLI 默认模式）+ API 双模式。用法：
    bash start.sh research SOXL --peers 5 --style analyst --disclaimer-agree
    bash start.sh api        # 默认端口 8000
"""
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

VENV_DIR="${VENV_DIR:-$SCRIPT_DIR/.venv}"

if [ ! -d "$VENV_DIR" ]; then
  echo "[start.sh] 首次启动：创建虚拟环境 $VENV_DIR + 安装依赖..."
  python3 -m venv "$VENV_DIR"
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
  python -m pip install --upgrade pip >/dev/null
  pip install -r requirements.txt
else
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
fi

MODE="${1:-research}"
shift || true

if [ "$MODE" = "api" ]; then
  echo "[start.sh] 启动 FastAPI（模式=api）..."
  exec python -m uvicorn backend.app:app --host 0.0.0.0 --port "${PORT:-8000}" --reload
fi

echo "[start.sh] 启动 CLI 研究（模式=research）..."
exec python main.py research "$@"
