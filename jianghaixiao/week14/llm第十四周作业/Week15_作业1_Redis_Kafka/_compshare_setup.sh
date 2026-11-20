#!/usr/bin/env bash
set -euo pipefail

WORKDIR=/workspace/week15
mkdir -p "$WORKDIR"
cd "$WORKDIR"

cat > redis_demo.py <<'PY'
import json
import os
import redis

client = redis.Redis.from_url(
    os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"),
    decode_responses=True,
)
key = "chat:history:user-001"
client.delete(key)

messages = [
    ("user", "请解释 Redis 和 Kafka 的区别。"),
    ("assistant", "Redis 偏重低延迟缓存，Kafka 偏重消息流传递。"),
    ("user", "聊天历史适合放在 Redis 吗？"),
    ("assistant", "最近聊天上下文适合放在 Redis，并设置过期时间。"),
]

for role, content in messages:
    client.lpush(
        key,
        json.dumps({"role": role, "content": content}, ensure_ascii=False),
    )

client.ltrim(key, 0, 19)
client.expire(key, 86400)

print("=== Redis 聊天历史 ===")
print("Redis key:", key)
print("消息数量:", client.llen(key))
for raw in reversed(client.lrange(key, 0, -1)):
    message = json.loads(raw)
    print(f"{message['role']}: {message['content']}")
PY

cat > kafka_demo.py <<'PY'
import json
import os
import sys
import uuid
from kafka import KafkaConsumer, KafkaProducer

broker = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "127.0.0.1:9092")
topic = os.getenv("KAFKA_TOPIC", "chat-messages")


def serialize(value):
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def deserialize(value):
    return json.loads(value.decode("utf-8"))


def produce():
    producer = KafkaProducer(
        bootstrap_servers=[broker],
        value_serializer=serialize,
        acks="all",
    )
    try:
        messages = [
            {"sender": "alice", "content": "第一条 Kafka 消息"},
            {"sender": "bob", "content": "第二条 Kafka 消息"},
        ]
        for message in messages:
            metadata = producer.send(topic, value=message).get(timeout=10)
            print(
                f"发送成功: partition={metadata.partition}, "
                f"offset={metadata.offset}, content={message['content']}"
            )
    finally:
        producer.close()


def consume():
    consumer = KafkaConsumer(
        topic,
        bootstrap_servers=[broker],
        group_id=f"week15-{uuid.uuid4().hex[:8]}",
        auto_offset_reset="earliest",
        consumer_timeout_ms=15000,
        value_deserializer=deserialize,
    )
    try:
        count = 0
        for record in consumer:
            message = record.value
            print(
                f"收到消息: partition={record.partition}, "
                f"offset={record.offset}, sender={message['sender']}, "
                f"content={message['content']}"
            )
            count += 1
            if count >= 2:
                break
    finally:
        consumer.close()


if __name__ == "__main__":
    if sys.argv[1] == "produce":
        produce()
    elif sys.argv[1] == "consume":
        consume()
    else:
        raise SystemExit("用法: python kafka_demo.py produce|consume")
PY

python -m pip install -q --upgrade pip
python -m pip install -q redis kafka-python

if ! redis-cli ping >/dev/null 2>&1; then
    if command -v redis-server >/dev/null 2>&1; then
        redis-server --daemonize yes
    else
        apt-get update -qq
        apt-get install -y -qq redis-server
        redis-server --daemonize yes
    fi
fi

echo "Redis 状态: $(redis-cli ping)"

BROKER="${KAFKA_BOOTSTRAP_SERVERS:-127.0.0.1:9092}"
case "$BROKER" in
    ""|*Kafka*|*地址*) BROKER="127.0.0.1:9092" ;;
esac
export KAFKA_BOOTSTRAP_SERVERS="$BROKER"
export KAFKA_TOPIC="chat-demo-$(date +%s)"

echo "Kafka 地址: $KAFKA_BOOTSTRAP_SERVERS"

python - "$BROKER" <<'PY'
import socket
import sys

host, port = sys.argv[1].rsplit(":", 1)
try:
    with socket.create_connection((host, int(port)), timeout=3):
        pass
    print(f"Kafka 端口可访问: {host}:{port}")
except OSError as exc:
    print(f"无法连接 Kafka {host}:{port}: {exc}")
    print("请检查监听端口：ss -ltnp | grep 9092")
    print("请检查 Kafka 进程：ps -ef | grep -E 'kafka|zookeeper' | grep -v grep")
    raise SystemExit(1)
PY

echo
echo "=== Redis 演示 ==="
python redis_demo.py

echo
echo "=== Kafka 演示 ==="
python kafka_demo.py consume > /tmp/kafka-consumer.log 2>&1 &
CONSUMER_PID=$!
sleep 3
python kafka_demo.py produce
wait "$CONSUMER_PID" || true
cat /tmp/kafka-consumer.log

echo
echo "完成。代码位于 $WORKDIR"