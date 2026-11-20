#!/usr/bin/env bash
# 只停止本项目启动的进程；Kafka / Elasticsearch / MinerU 由本机自行管理。
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

for name in api worker web; do
  pidfile=".runtime/${name}.pid"
  if [ -f "$pidfile" ]; then
    pid="$(cat "$pidfile")"
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" && echo "[$name] stopped ($pid)"
    fi
    rm -f "$pidfile"
  fi
done

echo "done"
