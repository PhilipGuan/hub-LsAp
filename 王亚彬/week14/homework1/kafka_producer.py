import json
import os
from kafka import KafkaProducer

BROKER = os.getenv("KAFKA_BROKER", "localhost:9092")
TOPIC = "chat-messages"

producer = KafkaProducer(
    bootstrap_servers=BROKER,
    value_serializer=lambda v: json.dumps(v, ensure_ascii=False).encode(),
)

for i in range(1, 4):
    producer.send(TOPIC, {"user_id": 1001, "text": f"第 {i} 条消息"})
producer.flush()
print("已发送 3 条消息")
