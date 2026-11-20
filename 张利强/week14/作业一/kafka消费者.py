# kafka_consumer.py
import json
from kafka import KafkaConsumer

consumer = KafkaConsumer(
    'test-events',
    bootstrap_servers=['localhost:9092'],
    group_id='event-processor-group',
    auto_offset_reset='earliest',      # 无偏移时从头读，方便测试; latest 从最新开始
    enable_auto_commit=False,          # 手动提交，保证处理完再提交
    value_deserializer=lambda v: json.loads(v.decode('utf-8'))
)

print("消费者启动，等待消息...")
try:
    for msg in consumer:
        event = msg.value
        print(f"收到事件: {event} | partition={msg.partition}, offset={msg.offset}")

        # 模拟业务处理
        # ...

        # 手动提交偏移量
        consumer.commit()
except KeyboardInterrupt:
    print("消费者停止")
finally:
    consumer.close()