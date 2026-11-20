"""Kafka worker that turns uploaded PDFs into searchable multimodal indexes."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal

from aiokafka import AIOKafkaConsumer

from .config import (
    INGEST_TOPIC,
    KAFKA_BOOTSTRAP_SERVERS,
    WORKER_GROUP,
)
from .db import STATUS_COMPLETED, get_document, init_db
from .ingestion import ingest_document
from .kafka_bus import KafkaPublisher, ensure_topics

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("mmrag.worker")


async def _handle_record(record, publisher: KafkaPublisher) -> None:
    try:
        payload = json.loads(record.value.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.warning("skip malformed Kafka record: %s", exc)
        return
    if payload.get("type") != "document.ingest":
        logger.warning("skip unknown Kafka event: %s", payload.get("type"))
        return
    document_id = payload.get("document_id")
    document = get_document(document_id) if document_id else None
    if not document:
        logger.warning("skip ingest for missing document %s", document_id)
        return
    if document["status"] == STATUS_COMPLETED:
        logger.info("skip already-completed document %s", document_id)
        return
    logger.info("picked up %s (%s)", document["filename"], document_id)
    try:
        await ingest_document(document, publisher)
    except Exception as exc:
        logger.error("ingest failed for %s: %s", document_id, exc)


async def run() -> None:
    init_db()
    publisher = KafkaPublisher()
    await publisher.start()
    if not publisher.ready:
        logger.warning("Kafka producer unavailable; status events may be lost")

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    consumer: AIOKafkaConsumer | None = None
    try:
        while not stop_event.is_set():
            try:
                await ensure_topics()
                consumer = AIOKafkaConsumer(
                    INGEST_TOPIC,
                    bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
                    group_id=WORKER_GROUP,
                    auto_offset_reset="earliest",
                    enable_auto_commit=False,
                    max_poll_records=1,
                    client_id="mmrag-ingest-worker",
                )
                await consumer.start()
                logger.info(
                    "worker connected: topic=%s group=%s bootstrap=%s",
                    INGEST_TOPIC,
                    WORKER_GROUP,
                    KAFKA_BOOTSTRAP_SERVERS,
                )
                while not stop_event.is_set():
                    batches = await consumer.getmany(timeout_ms=1000, max_records=1)
                    for records in batches.values():
                        for record in records:
                            await _handle_record(record, publisher)
                            await consumer.commit()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("worker Kafka loop error: %s", exc)
                await asyncio.sleep(2)
            finally:
                if consumer:
                    try:
                        await consumer.stop()
                    except Exception:
                        pass
                    consumer = None
    finally:
        await publisher.stop()
        logger.info("worker stopped")


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
