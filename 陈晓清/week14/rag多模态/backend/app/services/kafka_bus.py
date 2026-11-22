# -*- coding: utf-8 -*-
"""Kafka 生产/消费封装。连接不可用时由调用方走轮询兜底。"""

from __future__ import annotations

import json
import socket
from datetime import datetime
from typing import Callable

from ..core.config import KAFKA_BOOTSTRAP, KAFKA_GROUP, KAFKA_TOPIC


def _host_port() -> tuple[str, int]:
    host, _, port = KAFKA_BOOTSTRAP.partition(":")
    return host or "localhost", int(port or 9092)


def broker_available(timeout: float = 2.0) -> bool:
    host, port = _host_port()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _message(file_id: int, file_name: str, file_path: str) -> dict:
    return {
        "file_id": int(file_id),
        "file_name": file_name,
        "file_path": file_path,
        "requested_at": datetime.now().isoformat(timespec="seconds"),
    }


def publish(file_id: int, file_name: str, file_path: str) -> bool:
    try:
        from kafka import KafkaProducer

        producer = KafkaProducer(
            bootstrap_servers=KAFKA_BOOTSTRAP,
            value_serializer=lambda value: json.dumps(value, ensure_ascii=False).encode("utf-8"),
            bootstrap_timeout_ms=5000,
            request_timeout_ms=5000,
        )
        try:
            producer.send(KAFKA_TOPIC, _message(file_id, file_name, file_path)).get(timeout=10)
            producer.flush()
        finally:
            producer.close(timeout=5)
        return True
    except Exception:
        return False


def consume(handler: Callable[[dict], None], stop_event=None) -> None:
    from kafka import KafkaConsumer

    consumer = KafkaConsumer(
        KAFKA_TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP,
        group_id=KAFKA_GROUP,
        value_deserializer=lambda value: json.loads(value.decode("utf-8")),
        enable_auto_commit=False,
        auto_offset_reset="earliest",
        max_poll_interval_ms=1800000,
        session_timeout_ms=60000,
        request_timeout_ms=40000,
        bootstrap_timeout_ms=8000,
    )
    try:
        for message in consumer:
            if stop_event is not None and stop_event.is_set():
                break
            handler(message.value or {})
            consumer.commit()
    finally:
        try:
            consumer.close()
        except Exception:
            pass
