import json
import sys

from kafka import KafkaConsumer, KafkaProducer

BROKER = "127.0.0.1:9092"
TOPIC = "chat-events"
GROUP = "chat-demo"


def produce() -> None:
    p = KafkaProducer(
        bootstrap_servers=BROKER,
        value_serializer=lambda v: json.dumps(v, ensure_ascii=False).encode(),
    )
    for i in range(5):
        p.send(TOPIC, {"seq": i, "user": "u1", "text": f"第 {i} 条消息"}).get(timeout=10)
        print(f"sent {i}")
    p.flush()
    p.close()


def consume() -> None:
    c = KafkaConsumer(
        TOPIC,
        bootstrap_servers=BROKER,
        group_id=GROUP,
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        value_deserializer=lambda b: json.loads(b.decode()),
        consumer_timeout_ms=8000,
    )
    n = 0
    for m in c:
        print(f"recv p={m.partition} off={m.offset} {m.value}")
        n += 1
    c.close()
    assert n >= 5, f"预期至少 5 条，收到 {n}"
    print(f"OK: 消费 {n} 条")


if __name__ == "__main__":
    {"produce": produce, "consume": consume}[sys.argv[1]]()
