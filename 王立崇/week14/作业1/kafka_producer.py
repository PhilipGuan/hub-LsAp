# kafka_producer.py
import json
import time
from kafka import KafkaProducer

producer = KafkaProducer(
    bootstrap_servers="localhost:9092",
    value_serializer=lambda v: json.dumps(v, ensure_ascii=False).encode("utf-8"),
    key_serializer=lambda k: k.encode("utf-8") if k else None,
    acks="all",             # 等所有 ISR 确认，保证不丢
    linger_ms=10,           # 小批量聚合提升吞吐
)

TOPIC = "chat-events"


def send_chat_event(session_id: str, role: str, content: str):
    event = {
        "session_id": session_id,
        "role": role,
        "content": content,
        "ts": time.time(),
    }
    # 用 session_id 作 key → 同一会话消息落到同一分区，保证顺序
    future = producer.send(TOPIC, key=session_id, value=event)
    # 同步等待确认（生产环境可异步 + 回调）
    meta = future.get(timeout=10)
    print(f"sent -> partition={meta.partition}, offset={meta.offset}, "
          f"session={session_id}, content={content}")


if __name__ == "__main__":
    demo = [
        ("user_1001", "user", "你好，我叫小明"),
        ("user_1001", "assistant", "你好小明，有什么可以帮你？"),
        ("user_1001", "user", "帮我查下北京天气"),
        ("user_1002", "user", "另一个用户的对话"),
    ]
    for sid, role, content in demo:
        send_chat_event(sid, role, content)

    producer.flush()
    producer.close()