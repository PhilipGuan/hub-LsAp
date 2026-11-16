# kafka_consumer.py
import json
from kafka import KafkaConsumer

consumer = KafkaConsumer(
    "chat-events",
    bootstrap_servers="localhost:9092",
    group_id="chat-analytics",           # 消费组，支持多实例分摊
    auto_offset_reset="earliest",        # 无 offset 时从头读
    enable_auto_commit=True,
    value_deserializer=lambda b: json.loads(b.decode("utf-8")),
)

print("等待消息... (Ctrl+C 退出)")
try:
    for record in consumer:
        evt = record.value
        print(f"[{record.partition}:{record.offset}] "
              f"session={evt['session_id']} role={evt['role']} "
              f"content={evt['content']}")
except KeyboardInterrupt:
    print("\n关闭消费者")
finally:
    consumer.close()