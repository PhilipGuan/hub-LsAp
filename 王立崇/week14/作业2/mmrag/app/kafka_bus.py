"""Kafka plumbing: topic bootstrap, publisher, and status event fan-out."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.admin import AIOKafkaAdminClient, NewTopic

from .config import (
    API_EVENT_GROUP_PREFIX,
    INGEST_TOPIC,
    KAFKA_BOOTSTRAP_SERVERS,
    STATUS_TOPIC,
)
from .db import now_iso

logger = logging.getLogger(__name__)


async def ensure_topics() -> None:
    admin = AIOKafkaAdminClient(
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        request_timeout_ms=8000,
    )
    await admin.start()
    try:
        existing = set(await admin.list_topics())
        missing = [
            NewTopic(name=topic, num_partitions=1, replication_factor=1)
            for topic in (INGEST_TOPIC, STATUS_TOPIC)
            if topic not in existing
        ]
        if missing:
            await admin.create_topics(missing)
            logger.info("created Kafka topics: %s", [topic.name for topic in missing])
    finally:
        await admin.close()


@dataclass
class KafkaPublisher:
    producer: AIOKafkaProducer | None = None
    error: str | None = None

    async def start(self) -> None:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                producer = AIOKafkaProducer(
                    bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
                    acks="all",
                    enable_idempotence=True,
                    linger_ms=5,
                )
                await producer.start()
                self.producer = producer
                self.error = None
                logger.info("Kafka producer connected to %s", KAFKA_BOOTSTRAP_SERVERS)
                return
            except Exception as exc:
                last_error = exc
                logger.warning("Kafka producer attempt %s failed: %s", attempt + 1, exc)
                await asyncio.sleep(1.5)
        self.error = str(last_error)
        logger.error("Kafka producer unavailable: %s", last_error)

    async def stop(self) -> None:
        if self.producer:
            await self.producer.stop()
            self.producer = None

    @property
    def ready(self) -> bool:
        return self.producer is not None

    async def send(self, topic: str, payload: dict[str, Any]) -> None:
        if not self.producer:
            raise RuntimeError(f"Kafka producer is not ready: {self.error}")
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        await self.producer.send_and_wait(topic, body)

    async def enqueue_document(self, document: dict[str, Any]) -> None:
        await self.send(
            INGEST_TOPIC,
            {
                "type": "document.ingest",
                "document_id": document["id"],
                "filename": document["filename"],
                "path": document["stored_path"],
                "tier": document.get("tier"),
                "ts": now_iso(),
            },
        )

    async def publish_document(self, document: dict[str, Any]) -> None:
        try:
            await self.send(
                STATUS_TOPIC,
                {
                    "type": "document.status",
                    "document": document,
                    "ts": now_iso(),
                },
            )
        except Exception as exc:
            logger.warning("failed to publish status event: %s", exc)


class StatusEventBroker:
    """Consume the status topic and fan events out to SSE subscribers."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._task: asyncio.Task[None] | None = None
        self._stopped = False
        self._consumer: AIOKafkaConsumer | None = None
        self.group_id = f"{API_EVENT_GROUP_PREFIX}-{socket.gethostname()}-{os.getpid()}"

    async def start(self) -> None:
        self._stopped = False
        self._task = asyncio.create_task(self._run(), name="kafka-status-broker")

    async def stop(self) -> None:
        self._stopped = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._consumer:
            try:
                await self._consumer.stop()
            except Exception:
                pass
            self._consumer = None

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[dict[str, Any]]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)

    def _broadcast(self, event: dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    async def _run(self) -> None:
        while not self._stopped:
            try:
                await ensure_topics()
                break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("status broker waiting for Kafka: %s", exc)
                await asyncio.sleep(2)
        if self._stopped:
            return

        while not self._stopped:
            consumer: AIOKafkaConsumer | None = None
            try:
                consumer = AIOKafkaConsumer(
                    STATUS_TOPIC,
                    bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
                    group_id=self.group_id,
                    auto_offset_reset="latest",
                    enable_auto_commit=True,
                    client_id=f"mmrag-events-{uuid.uuid4().hex[:8]}",
                )
                await consumer.start()
                self._consumer = consumer
                logger.info("status event consumer connected (%s)", self.group_id)
                while not self._stopped:
                    batches = await consumer.getmany(timeout_ms=1000, max_records=50)
                    for records in batches.values():
                        for record in records:
                            try:
                                event = json.loads(record.value.decode("utf-8"))
                            except (UnicodeDecodeError, json.JSONDecodeError):
                                continue
                            self._broadcast(event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("status event consumer error: %s", exc)
                await asyncio.sleep(2)
            finally:
                if consumer:
                    try:
                        await consumer.stop()
                    except Exception:
                        pass
                    if consumer is self._consumer:
                        self._consumer = None
