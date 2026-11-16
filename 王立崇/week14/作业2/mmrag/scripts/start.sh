#!/usr/bin/env bash
# Start the FastAPI API and the Kafka ingestion worker in the background.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python}"
mkdir -p data/logs data/uploads data/parsed

# Read web settings through config.py so values in .env are honoured.
read -r HOST PORT < <("$PYTHON" -c 'from app.config import APP_HOST, APP_PORT; print(APP_HOST, APP_PORT)' 2>/dev/null)
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8080}"

pid_running() {
  local pidfile="$1"
  [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null
}

start_api() {
  if pid_running data/api.pid; then
    echo "API already running (pid $(cat data/api.pid))"
    return
  fi
  nohup "$PYTHON" -m uvicorn app.main:app --host "$HOST" --port "$PORT" \
    >> data/logs/api.log 2>&1 &
  echo $! > data/api.pid
  echo "API started (pid $(cat data/api.pid), http://localhost:$PORT)"
}

start_worker() {
  if pid_running data/worker.pid; then
    echo "Worker already running (pid $(cat data/worker.pid))"
    return
  fi
  nohup "$PYTHON" -m app.worker >> data/logs/worker.log 2>&1 &
  echo $! > data/worker.pid
  echo "Worker started (pid $(cat data/worker.pid))"
}

start_api
start_worker

# Give the API a moment to connect to Kafka and the LLM, then show a quick hint.
for _ in $(seq 1 20); do
  if curl -fsS "http://localhost:$PORT/api/health" > data/logs/last-health.json 2>/dev/null; then
    "$PYTHON" - <<'PY'
import json
from pathlib import Path
health = json.loads(Path("data/logs/last-health.json").read_text())
print(
    "Kafka:", "ok" if health["kafka"]["ready"] else "DOWN",
    "| LLM:", "ok" if health["llm"]["ready"] else "DOWN",
    "| MinerU:", "ok" if health["mineru"]["ready"] else "DOWN",
)
PY
    echo "Open http://localhost:$PORT"
    exit 0
  fi
  sleep 0.5
done

echo "API did not answer yet. Check data/logs/api.log" >&2
exit 1
