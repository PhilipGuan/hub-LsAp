#!/usr/bin/env bash
# 本地 KRaft Kafka：数据与日志全部放在项目内 .runtime/，不依赖 Docker。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KAFKA_HOME="${KAFKA_HOME:-$(brew --prefix kafka 2>/dev/null || true)}"
RUNTIME="$ROOT/.runtime"
DATA_DIR="$RUNTIME/kafka-data"
CONFIG_DIR="$RUNTIME/kafka"
CONFIG="$CONFIG_DIR/server.properties"
LOG="$RUNTIME/kafka.log"
PID_FILE="$RUNTIME/kafka.pid"

if [ -z "${KAFKA_HOME:-}" ] || [ ! -x "$KAFKA_HOME/bin/kafka-server-start" ]; then
  echo "找不到 Kafka。请先 brew install kafka，或设置 KAFKA_HOME 指向 Kafka 安装目录。" >&2
  exit 1
fi

port_open() {
  nc -z -G 1 127.0.0.1 9092 >/dev/null 2>&1
}

write_config() {
  mkdir -p "$DATA_DIR" "$CONFIG_DIR"
  cat > "$CONFIG" <<EOF
process.roles=broker,controller
node.id=1
controller.quorum.voters=1@127.0.0.1:19093
listeners=PLAINTEXT://127.0.0.1:9092,CONTROLLER://127.0.0.1:19093
advertised.listeners=PLAINTEXT://localhost:9092
inter.broker.listener.name=PLAINTEXT
controller.listener.names=CONTROLLER
listener.security.protocol.map=CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT
log.dirs=$DATA_DIR
num.partitions=1
default.replication.factor=1
offsets.topic.replication.factor=1
transaction.state.log.replication.factor=1
transaction.state.log.min.isr=1
group.initial.rebalance.delay.ms=0
auto.create.topics.enable=true
EOF
}

start() {
  if port_open; then
    echo "Kafka 已在 localhost:9092 运行"
    return 0
  fi

  write_config
  if [ ! -f "$DATA_DIR/meta.properties" ]; then
    local uuid
    uuid="$("$KAFKA_HOME/bin/kafka-storage" random-uuid)"
    "$KAFKA_HOME/bin/kafka-storage" format -t "$uuid" -c "$CONFIG" >/dev/null
    echo "已格式化 KRaft 存储：$DATA_DIR"
  fi

  mkdir -p "$RUNTIME"
  KAFKA_HEAP_OPTS="${KAFKA_HEAP_OPTS:--Xms256m -Xmx512m}" \
    nohup "$KAFKA_HOME/bin/kafka-server-start" "$CONFIG" >"$LOG" 2>&1 &
  echo $! >"$PID_FILE"

  for _ in $(seq 1 60); do
    if port_open; then
      echo "Kafka 已启动 localhost:9092 (pid $(cat "$PID_FILE"))"
      return 0
    fi
    sleep 1
  done

  echo "Kafka 启动失败，最近日志：" >&2
  tail -30 "$LOG" >&2
  exit 1
}

stop() {
  if [ -f "$PID_FILE" ]; then
    kill "$(cat "$PID_FILE")" 2>/dev/null || true
    rm -f "$PID_FILE"
  fi
  for _ in $(seq 1 20); do
    if ! port_open; then
      echo "Kafka 已停止"
      return 0
    fi
    sleep 1
  done
  echo "Kafka 仍在运行，请手动检查" >&2
  exit 1
}

status() {
  if port_open; then
    echo "Kafka 运行中：localhost:9092"
  else
    echo "Kafka 未运行"
    exit 1
  fi
}

case "${1:-start}" in
  start) start ;;
  stop) stop ;;
  restart) stop || true; start ;;
  status) status ;;
  *) echo "用法：$0 {start|stop|restart|status}" >&2; exit 1 ;;
esac
