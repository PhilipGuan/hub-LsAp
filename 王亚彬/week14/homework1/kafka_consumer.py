import json
import os
from kafka import KafkaConsumer

BROKER = os.getenv("KAFKA_BROKER", "localhost:9092")
TOPIC = "chat-messages"

consumer = KafkaConsumer(
    TOPIC,
    bootstrap_servers=BROKER,
    auto_offset_reset="earliest",
    group_id="chat-demo",
    enable_auto_commit=False,
    value_deserializer=lambda v: json.loads(v.decode()),
    consumer_timeout_ms=10000,
)

for msg in consumer:
    print(f"partition={msg.partition} offset={msg.offset} -> {msg.value}")
