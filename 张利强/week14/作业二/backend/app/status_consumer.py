"""API 进程内的 Kafka 状态消费者：doc-status → 进程内事件总线 → SSE。"""

import logging

from .config import get_settings
from .events import bus
from .kafka_client import make_consumer

log = logging.getLogger(__name__)
settings = get_settings()


def status_consumer_loop() -> None:
    consumer = make_consumer(settings.kafka_topic_status, "api-status")
    log.info("status consumer started: %s", settings.kafka_topic_status)
    try:
        while True:
            for _tp, messages in consumer.poll(timeout_ms=1000).items():
                for message in messages:
                    payload = {**message.value, "type": "doc_status"}
                    bus.publish_threadsafe(payload)
    except Exception:  # noqa: BLE001
        log.exception("status consumer crashed")
    finally:
        consumer.close()
