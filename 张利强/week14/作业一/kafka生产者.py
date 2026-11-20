# kafka_producer.py
import json
import time
from kafka import KafkaProducer

producer = KafkaProducer(
    bootstrap_servers=['localhost:9092'],
    value_serializer=lambda v: json.dumps(v).encode('utf-8'),
    acks='all',
    retries=3
)

topic = 'test-events'

def send_event(event_id: int, user: str, amount: float):
    message = {
        "event_id": event_id,
        "user": user,
        "amount": amount,
        "timestamp": time.time()
    }
    future = producer.send(topic, value=message)
    # 阻塞等待确认（生产环境可按需调整）
    result = future.get(timeout=10)
    print(f"已发送: {message} -> partition={result.partition}, offset={result.offset}")

if __name__ == "__main__":
    for i in range(1, 6):
        send_event(i, f"user_{i}", 99.9 * i)
        time.sleep(0.5)

    producer.flush()
    producer.close()
    print("生产者结束")