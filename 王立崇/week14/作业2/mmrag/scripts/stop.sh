#!/usr/bin/env bash
# Stop processes tracked by data/api.pid and data/worker.pid.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

stop_one() {
  local name="$1"
  local pidfile="data/${name}.pid"
  if [[ ! -f "$pidfile" ]]; then
    echo "$name is not tracked (no $pidfile)"
    return
  fi
  local pid
  pid="$(cat "$pidfile")"
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "$name already stopped (stale pid $pid)"
    rm -f "$pidfile"
    return
  fi
  echo "Stopping $name (pid $pid)"
  kill "$pid" 2>/dev/null || true
  for _ in $(seq 1 30); do
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.2
  done
  if kill -0 "$pid" 2>/dev/null; then
    echo "$name did not stop gracefully; killing"
    kill -9 "$pid" 2>/dev/null || true
  fi
  rm -f "$pidfile"
}

stop_one api
stop_one worker
