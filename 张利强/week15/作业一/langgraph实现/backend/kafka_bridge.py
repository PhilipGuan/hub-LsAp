"""Kafka Producer/Consumer 封装 + correlation_id 请求-回复路由。

Kafka 原生是发布-订阅；请求-回复语义靠进程内注册表实现：
每个 /chat 请求生成一个 correlation_id，注册一个 Pending（内含两个队列），
后台消费者按 correlation_id 把消息投递到对应队列。
仅支持单实例后端；多实例需把注册表换成 Redis 等共享存储。
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

from .config import settings

logger = logging.getLogger(__name__)


def json_serializer(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def json_deserializer(raw: bytes) -> Any:
    return json.loads(raw.decode("utf-8"))


@dataclass
class Pending:
    """一个请求在途期间的收件箱。"""

    emotion: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)
    reply: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)


_pending: dict[str, Pending] = {}


def register_pending(correlation_id: str) -> Pending:
    pending = Pending()
    _pending[correlation_id] = pending
    return pending


def unregister_pending(correlation_id: str) -> None:
    _pending.pop(correlation_id, None)


class KafkaBridge:
    """后端进程内的 Kafka 门面：一个 Producer + 两个结果 Consumer。"""

    def __init__(self) -> None:
        self._producer: AIOKafkaProducer | None = None
        self._consumers: list[AIOKafkaConsumer] = []
        self._tasks: list[asyncio.Task[None]] = []

    async def start(self) -> None:
        self._producer = AIOKafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap,
            value_serializer=json_serializer,
            acks="all",
        )
        await self._producer.start()

        await self._start_consumer(
            settings.topic_emotion_result, settings.backend_emotion_group, self._on_emotion_result
        )
        await self._start_consumer(
            settings.topic_reply_token, settings.backend_reply_group, self._on_reply_token
        )

    async def _start_consumer(self, topic: str, group_id: str, handler) -> None:
        consumer = AIOKafkaConsumer(
            topic,
            bootstrap_servers=settings.kafka_bootstrap,
            group_id=group_id,
            value_deserializer=json_deserializer,
            auto_offset_reset="latest",
            enable_auto_commit=True,
        )
        await consumer.start()
        self._consumers.append(consumer)
        task = asyncio.create_task(self._drain(consumer, topic, handler), name=f"consume-{topic}")
        self._tasks.append(task)

    async def _drain(self, consumer: AIOKafkaConsumer, topic: str, handler) -> None:
        try:
            async for message in consumer:
                data = message.value
                if not isinstance(data, dict):
                    logger.warning("忽略非 JSON 对象消息 topic=%s", topic)
                    continue
                try:
                    handler(data)
                except Exception:  # 单条消息失败不能拖垮消费者
                    logger.exception("处理消息失败 topic=%s", topic)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("消费者异常退出 topic=%s", topic)

    def _on_emotion_result(self, data: dict[str, Any]) -> None:
        pending = _pending.get(str(data.get("correlation_id", "")))
        if pending is None:
            logger.debug("丢弃无主情感结果 correlation_id=%s", data.get("correlation_id"))
            return
        pending.emotion.put_nowait(data)

    def _on_reply_token(self, data: dict[str, Any]) -> None:
        pending = _pending.get(str(data.get("correlation_id", "")))
        if pending is None:
            logger.debug("丢弃无主回复 token correlation_id=%s", data.get("correlation_id"))
            return
        pending.reply.put_nowait(data)

    async def send(self, topic: str, payload: dict[str, Any]) -> None:
        if self._producer is None:
            raise RuntimeError("KafkaBridge 尚未启动")
        await self._producer.send_and_wait(topic, payload)

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

        for consumer in self._consumers:
            try:
                await consumer.stop()
            except Exception:
                logger.exception("关闭消费者失败")
        self._consumers.clear()

        if self._producer is not None:
            try:
                await self._producer.stop()
            except Exception:
                logger.exception("关闭 Producer 失败")
            self._producer = None

        _pending.clear()
