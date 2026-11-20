#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
exec "$ROOT/.venv/bin/python" -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000 "$@"
