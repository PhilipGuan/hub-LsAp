"""Kafka 生产者/消费者工厂与 Topic 初始化。

模块自测：python -m shared.kafka_config
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.admin import AIOKafkaAdminClient, NewTopic

from shared.config import KafkaSettings, load_kafka_settings


def json_serializer(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")


def json_deserializer(value: bytes) -> dict:
    return json.loads(value.decode("utf-8"))


def create_producer(settings: KafkaSettings | None = None) -> AIOKafkaProducer:
    settings = settings or load_kafka_settings()
    return AIOKafkaProducer(
        bootstrap_servers=settings.bootstrap_servers,
        key_serializer=lambda key: key.encode("utf-8"),
        value_serializer=json_serializer,
    )


def create_consumer(
    topics: list[str],
    group_id: str,
    settings: KafkaSettings | None = None,
    *,
    auto_offset_reset: str = "earliest",
    enable_auto_commit: bool = True,
) -> AIOKafkaConsumer:
    settings = settings or load_kafka_settings()
    return AIOKafkaConsumer(
        *topics,
        bootstrap_servers=settings.bootstrap_servers,
        group_id=group_id,
        value_deserializer=json_deserializer,
        auto_offset_reset=auto_offset_reset,
        enable_auto_commit=enable_auto_commit,
    )


async def ensure_topics(settings: KafkaSettings | None = None) -> None:
    """创建单分区 Topic，已存在则跳过。"""
    settings = settings or load_kafka_settings()
    admin = AIOKafkaAdminClient(bootstrap_servers=settings.bootstrap_servers)
    await admin.start()
    try:
        existing = set(await admin.list_topics())
        missing = [
            NewTopic(name=name, num_partitions=1, replication_factor=1)
            for name in (settings.raw_input_topic, settings.sentiment_result_topic)
            if name not in existing
        ]
        if missing:
            await admin.create_topics(missing)
    finally:
        await admin.close()


async def main() -> None:
    """自测：建 Topic → 生产一条消息 → 用独立消费组读回。"""
    settings = load_kafka_settings()
    await ensure_topics(settings)
    producer = create_producer(settings)
    consumer = create_consumer(
        [settings.raw_input_topic],
        f"kafka-config-selftest-{uuid.uuid4().hex[:8]}",
        settings,
    )
    await producer.start()
    await consumer.start()
    key = f"selftest-{uuid.uuid4().hex[:8]}"
    payload = {"request_id": key, "text": "kafka_config 自测消息"}
    try:
        await producer.send_and_wait(settings.raw_input_topic, key=key, value=payload)
        print("已发送:", payload)

        async def wait_for_message() -> dict:
            async for record in consumer:
                if record.key and record.key.decode("utf-8") == key:
                    return record.value
            raise RuntimeError("消费者提前结束")

        received = await asyncio.wait_for(wait_for_message(), timeout=10)
        print("已消费:", received)
    finally:
        await consumer.stop()
        await producer.stop()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
