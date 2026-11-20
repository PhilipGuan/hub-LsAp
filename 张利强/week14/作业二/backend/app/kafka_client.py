import json
import logging
import threading
from typing import Any

from kafka import KafkaConsumer, KafkaProducer

from .config import get_settings

log = logging.getLogger(__name__)
settings = get_settings()

_producer: KafkaProducer | None = None
_producer_lock = threading.Lock()


def _dumps(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")


def get_producer() -> KafkaProducer:
    global _producer
    if _producer is None:
        with _producer_lock:
            if _producer is None:
                _producer = KafkaProducer(
                    bootstrap_servers=settings.kafka_bootstrap,
                    value_serializer=_dumps,
                    key_serializer=lambda k: k.encode("utf-8") if isinstance(k, str) else k,
                    acks="all",
                    retries=3,
                    linger_ms=10,
                )
    return _producer


def publish_parse_task(doc_id: str, file_path: str, filename: str) -> None:
    producer = get_producer()
    producer.send(
        settings.kafka_topic_parse,
        key=doc_id,
        value={"doc_id": doc_id, "file_path": file_path, "filename": filename},
    ).get(timeout=10)


def publish_status(payload: dict[str, Any]) -> None:
    """把文档状态发到 Kafka，由 API 进程消费后推给 SSE。"""
    try:
        get_producer().send(
            settings.kafka_topic_status, key=payload.get("doc_id", ""), value=payload
        )
    except Exception:  # 状态推送失败不应影响主流程
        log.exception("publish_status failed: %s", payload)


def publish_dlq(payload: dict[str, Any]) -> None:
    get_producer().send(settings.kafka_topic_dlq, key=payload.get("doc_id", ""), value=payload)


def make_consumer(topic: str, group_id: str) -> KafkaConsumer:
    return KafkaConsumer(
        topic,
        bootstrap_servers=settings.kafka_bootstrap,
        group_id=group_id,
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        max_poll_records=1,
        value_deserializer=lambda b: json.loads(b.decode("utf-8")),
        key_deserializer=lambda b: b.decode("utf-8") if b else None,
        consumer_timeout_ms=1000,
    )
