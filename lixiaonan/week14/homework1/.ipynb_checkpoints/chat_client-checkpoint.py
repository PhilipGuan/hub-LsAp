#!/bin/python

import json
import time
from kafka import KafkaProducer

# 1. 连接 Kafka 生产者
producer = KafkaProducer(
    bootstrap_servers='127.0.0.1:9092',
    value_serializer=lambda v: json.dumps(v).encode('utf-8') # 把字典转成 JSON 字节流
)

def send_message(user_id, message):
    """将消息发送到 Kafka"""
    payload = {
        "user_id": user_id,
        "message": message,
        "timestamp": time.time()
    }
    # 发送到 topic
    producer.send('chat-topic', payload)
    producer.flush() # 强制发送
    print(f"✅ 已发送消息到 Kafka: {payload}")

if __name__ == "__main__":
    print("👤 用户客户端已就绪。")
    # 模拟用户 "张三" 连续发送 3 条消息
    send_message("zhangsan", "你好，我叫张三。")
    time.sleep(2) # 停顿两秒模拟真实打字
    send_message("zhangsan", "我最近在学习 Redis 和 Kafka。")
    time.sleep(2)
    send_message("zhangsan", "你能记住我的名字吗？")