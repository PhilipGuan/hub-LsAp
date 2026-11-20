# pipeline_consumer.py
import json
from kafka import KafkaConsumer
import redis

r = redis.Redis(host="localhost", port=6379, decode_responses=True)

consumer = KafkaConsumer(
    "chat-events",
    bootstrap_servers="localhost:9092",
    group_id="chat-persist",       # 独立消费组，不影响 analytics
    auto_offset_reset="earliest",
    value_deserializer=lambda b: json.loads(b.decode("utf-8")),
)

print("持久化消费者启动...")
for record in consumer:
    evt = record.value
    key = f"chat:{evt['session_id']}"
    r.rpush(key, json.dumps(evt, ensure_ascii=False))
    r.ltrim(key, -100, -1)
    r.expire(key, 3600)
    print(f"持久化: {evt['session_id']} <- {evt['content']}")