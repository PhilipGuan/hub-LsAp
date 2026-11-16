#!/usr/bin/env python3
"""用 Kafka 发送和消费聊天消息。

运行方式：
    python kafka_message_demo.py consume --group homework-001
    python kafka_message_demo.py send --sender alice --content "你好"

Kafka 与 Redis 的侧重点不同：
- Redis：保存“最近上下文”，读取快，适合聊天窗口的短期记忆。
- Kafka：把消息作为事件流传递，支持多个消费者、缓冲和异步处理。

本程序发送 JSON 消息，使用 group_id 让同一个消费者组分担分区中的消息，
并在成功打印消息后手动提交 offset。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Any

from kafka import KafkaConsumer, KafkaProducer
from kafka.errors import KafkaError, NoBrokersAvailable


DEFAULT_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
DEFAULT_TOPIC = os.getenv("KAFKA_TOPIC", "chat-messages")


def parse_servers(value: str) -> list[str]:
    servers = [item.strip() for item in value.split(",") if item.strip()]
    if not servers:
        raise ValueError("Kafka broker 地址不能为空")
    return servers


def json_serializer(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def json_deserializer(raw: bytes) -> dict[str, Any]:
    return json.loads(raw.decode("utf-8"))


def build_message(sender: str, content: str) -> dict[str, Any]:
    sender = sender.strip()
    content = content.strip()
    if not sender:
        raise ValueError("sender 不能为空")
    if not content:
        raise ValueError("content 不能为空")
    return {
        "message_id": uuid.uuid4().hex,
        "sender": sender,
        "content": content,
        "sent_at": datetime.now(timezone.utc).isoformat(),
    }


def create_producer(bootstrap_servers: str) -> KafkaProducer:
    return KafkaProducer(
        bootstrap_servers=parse_servers(bootstrap_servers),
        key_serializer=lambda key: key.encode("utf-8"),
        value_serializer=json_serializer,
        acks="all",
        retries=3,
        linger_ms=10,
        request_timeout_ms=15000,
    )


def send_message(args: argparse.Namespace) -> int:
    message = build_message(args.sender, args.content)
    producer = create_producer(args.bootstrap_servers)
    try:
        future = producer.send(args.topic, key=message["sender"], value=message)
        metadata = future.get(timeout=15)
        producer.flush()
        print(
            "已发送："
            f"topic={metadata.topic}, partition={metadata.partition}, "
            f"offset={metadata.offset}, message_id={message['message_id']}"
        )
        return 0
    finally:
        producer.close(timeout=10)


def create_consumer(args: argparse.Namespace) -> KafkaConsumer:
    return KafkaConsumer(
        args.topic,
        bootstrap_servers=parse_servers(args.bootstrap_servers),
        group_id=args.group,
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        value_deserializer=json_deserializer,
        consumer_timeout_ms=args.idle_timeout * 1000,
        client_id=f"week15-consumer-{uuid.uuid4().hex[:8]}",
    )


def consume_messages(args: argparse.Namespace) -> int:
    consumer = create_consumer(args)
    received = 0
    try:
        print(
            f"开始消费：topic={args.topic}, group={args.group}, "
            f"bootstrap_servers={args.bootstrap_servers}"
        )
        for record in consumer:
            value = record.value
            print(
                f"收到消息：partition={record.partition}, offset={record.offset}, "
                f"sender={value.get('sender')}, content={value.get('content')!r}, "
                f"sent_at={value.get('sent_at')}"
            )
            # 演示“至少一次”处理：只有打印成功后才提交 offset。
            consumer.commit()
            received += 1
            if args.max_messages and received >= args.max_messages:
                break
        print(f"消费结束，共处理 {received} 条消息。")
        return 0
    finally:
        consumer.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="用 Kafka 传递聊天消息")
    parser.add_argument(
        "--bootstrap-servers",
        default=DEFAULT_BOOTSTRAP_SERVERS,
        help="Kafka broker，多个地址用逗号分隔",
    )
    parser.add_argument("--topic", default=DEFAULT_TOPIC, help="Kafka topic")

    subparsers = parser.add_subparsers(dest="command", required=True)

    send_parser = subparsers.add_parser("send", help="发送一条聊天消息")
    send_parser.add_argument("--sender", required=True, help="发送者")
    send_parser.add_argument("--content", required=True, help="消息正文")

    consume_parser = subparsers.add_parser("consume", help="消费聊天消息")
    consume_parser.add_argument("--group", default="chat-demo-group", help="消费者组 ID")
    consume_parser.add_argument(
        "--max-messages",
        type=int,
        default=0,
        help="最多消费多少条；0 表示一直运行到空闲超时或 Ctrl+C",
    )
    consume_parser.add_argument(
        "--idle-timeout",
        type=int,
        default=30,
        help="连续多少秒没有新消息后退出",
    )

    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "send":
            return send_message(args)
        return consume_messages(args)
    except NoBrokersAvailable:
        print(
            "无法连接 Kafka。请先启动 Kafka，并检查 KAFKA_BOOTSTRAP_SERVERS 或 --bootstrap-servers。",
            file=sys.stderr,
        )
        return 1
    except KafkaError as exc:
        print(f"Kafka 操作失败：{exc}", file=sys.stderr)
        return 1
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"参数或消息格式错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())