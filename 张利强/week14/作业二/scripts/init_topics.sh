#!/usr/bin/env bash
# 仅创建应用所需的 topic；Kafka 由本机安装并运行，项目不负责安装/启动。
set -euo pipefail

BOOTSTRAP="${KAFKA_BOOTSTRAP:-127.0.0.1:9092}"

if ! kafka-topics --bootstrap-server "$BOOTSTRAP" --list >/dev/null 2>&1; then
  echo "[kafka] $BOOTSTRAP 不可用：请先启动本机 Kafka" >&2
  exit 1
fi

kafka-topics --bootstrap-server "$BOOTSTRAP" --create --if-not-exists \
  --topic pdf-parse --partitions 3 --replication-factor 1 >/dev/null
kafka-topics --bootstrap-server "$BOOTSTRAP" --create --if-not-exists \
  --topic pdf-parse-dlq --partitions 1 --replication-factor 1 >/dev/null
kafka-topics --bootstrap-server "$BOOTSTRAP" --create --if-not-exists \
  --topic doc-status --partitions 1 --replication-factor 1 >/dev/null

echo "[kafka] topics ready:"
kafka-topics --bootstrap-server "$BOOTSTRAP" --list
