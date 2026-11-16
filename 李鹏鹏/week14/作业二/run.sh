#!/usr/bin/env bash
# Linux / macOS 启动脚本
set -euo pipefail
cd "$(dirname "$0")"

PYTHON_BIN="${PYTHON:-python3}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "找不到 Python：$PYTHON_BIN（可用 PYTHON=/path/to/python 指定）" >&2
  exit 1
fi

exec "$PYTHON_BIN" run.py "$@"
