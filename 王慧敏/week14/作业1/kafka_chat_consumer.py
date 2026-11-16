# -*- coding: utf-8 -*-
"""Kafka 消费者示例

依赖:
    pip install confluent-kafka

前置:
    - 启动 Kafka
    - 与 producer 使用同一个 topic（默认 chat-messages）
"""

import json

from confluent_kafka import Consumer


def get_consumer(bootstrap_servers="localhost:9092", group_id="chat-worker",
                 topics=("chat-messages",)):
    c = Consumer({
        "bootstrap.servers": bootstrap_servers,
        "group.id": group_id,              # 消费者组：组内每条消息只被一个实例处理
        "auto.offset.reset": "earliest",   # 首次消费从最早消息开始（"latest" 则只收新消息）
        "enable.auto.commit": False,       # 关闭自动提交，改为处理成功后手动提交
    })
    c.subscribe(list(topics))
    return c


def consume(consumer, handler=None, timeout=1.0):
    """持续消费消息。

    :param consumer: Consumer 实例
    :param handler:  处理函数 handler(key: str, value: dict) -> None
    """
    try:
        while True:
            msg = consumer.poll(timeout=timeout)
            if msg is None:
                continue
            if msg.error():
                print(f"消费错误: {msg.error()}")
                continue

            key = msg.key().decode("utf-8") if msg.key() else None
            data = json.loads(msg.value().decode("utf-8"))
            print(f"收到消息 key={key} -> {data}")

            if handler:
                handler(key, data)

            # 处理成功后再提交 offset，避免"还没处理就标记为已消费"导致丢消息
            consumer.commit()
    except KeyboardInterrupt:
        print("\n消费者已停止")
    finally:
        consumer.close()


if __name__ == "__main__":
    # 示例：消费 Kafka 消息后，写入 Redis 历史（演示 Kafka 与 Redis 串联）
    from redis_chat_history import get_client, save_message

    redis_client = get_client()

    def handle(key, data):
        save_message(redis_client, data["session_id"], data["role"], data["content"])

    c = get_consumer()
    consume(c, handler=handle)
