# -*- coding: utf-8 -*-
"""Kafka 生产者示例

依赖:
    pip install confluent-kafka

前置:
    - 启动 Kafka（默认 localhost:9092）
    - 创建 topic，例如:
      kafka-topics --create --topic chat-messages --bootstrap-server localhost:9092 \
                   --partitions 3 --replication-factor 1
"""

import json

from confluent_kafka import Producer


def delivery_report(err, msg):
    """发送结果回调。"""
    if err is not None:
        print(f"发送失败: {err}")
    else:
        print(f"已发送到 {msg.topic()} 分区[{msg.partition()}] offset={msg.offset()}")


def get_producer(bootstrap_servers="localhost:9092", client_id="chat-producer"):
    return Producer({
        "bootstrap.servers": bootstrap_servers,
        "client.id": client_id,
        "acks": "all",          # 等待所有副本确认，保证消息不丢失（配合 retries 更稳）
        "retries": 3,
    })


def send_message(producer, session_id, role, content, topic="chat-messages"):
    """发送一条消息。

    key 设为 session_id：相同 key 会路由到同一分区，从而保证同一会话的消息有序。
    """
    msg = {"session_id": session_id, "role": role, "content": content}
    producer.produce(
        topic=topic,
        key=session_id,
        value=json.dumps(msg, ensure_ascii=False),
        callback=delivery_report,
    )


if __name__ == "__main__":
    p = get_producer()
    send_message(p, "sess_001", "user", "你好")
    p.flush()  # 阻塞，等待积压消息全部发送完成
