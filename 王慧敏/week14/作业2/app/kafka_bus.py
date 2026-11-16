# -*- coding: utf-8 -*-
"""Kafka 生产者/消费者封装（通用版）。

设计上延续项目里 kafka_chat_producer.py / kafka_chat_consumer.py 的风格：
    - key 用 doc_id，保证同一文档的消息进同一分区、按序处理
    - 生产者 acks=all + retries，消费者手动提交 offset（处理成功后才提交）

本文件被 app/main.py（生产解析任务）和 app/worker.py（消费解析任务）共用。
"""

import json

from confluent_kafka import Consumer, Producer

from app.config import KAFKA_BOOTSTRAP_SERVERS, KAFKA_GROUP_ID, KAFKA_TOPIC_PARSE_TASK


def _delivery_report(err, msg):
    if err is not None:
        print(f"[kafka] 发送失败: {err}")
    else:
        print(f"[kafka] 已发送到 {msg.topic()} 分区[{msg.partition()}] offset={msg.offset()}")


_producer = None


def get_producer():
    global _producer
    if _producer is None:
        _producer = Producer({
            "bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS,
            "client.id": "pdf-rag-producer",
            "acks": "all",
            "retries": 3,
        })
    return _producer


def send_parse_task(doc_id, filename, file_path):
    """发送一条"解析 PDF"任务消息到 Kafka。"""
    producer = get_producer()
    payload = {"doc_id": doc_id, "filename": filename, "file_path": file_path}
    producer.produce(
        topic=KAFKA_TOPIC_PARSE_TASK,
        key=doc_id,
        value=json.dumps(payload, ensure_ascii=False),
        callback=_delivery_report,
    )
    producer.flush(timeout=5)


def get_consumer(group_id=KAFKA_GROUP_ID, topics=(KAFKA_TOPIC_PARSE_TASK,)):
    consumer = Consumer({
        "bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS,
        "group.id": group_id,
        "auto.offset.reset": "earliest",
        "enable.auto.commit": False,
    })
    consumer.subscribe(list(topics))
    return consumer


def consume_loop(consumer, handler, timeout=1.0):
    """持续消费消息，处理成功后才手动提交 offset。

    :param handler: handler(key: str, data: dict) -> None，抛异常表示处理失败（不会提交 offset）
    """
    try:
        while True:
            msg = consumer.poll(timeout=timeout)
            if msg is None:
                continue
            if msg.error():
                print(f"[kafka] 消费错误: {msg.error()}")
                continue

            key = msg.key().decode("utf-8") if msg.key() else None
            data = json.loads(msg.value().decode("utf-8"))
            try:
                handler(key, data)
                consumer.commit()
            except Exception as exc:  # noqa: BLE001 - 教学 demo，打印即可
                print(f"[kafka] 处理消息失败，不提交 offset，等待重试: {exc}")
    except KeyboardInterrupt:
        print("\n[kafka] 消费者已停止")
    finally:
        consumer.close()
