#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"
exec "$ROOT/.venv/bin/python" -m app.workers.parse_worker
