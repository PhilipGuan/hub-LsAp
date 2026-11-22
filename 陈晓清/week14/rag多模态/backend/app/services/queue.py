# -*- coding: utf-8 -*-
"""入队策略：优先 Kafka；不可用时保持 pending，交给 worker 轮询。"""

from __future__ import annotations

from ..core.config import KAFKA_ENABLED
from . import kafka_bus


def enqueue_file(file_id: int, file_name: str, file_path: str) -> bool:
    if not KAFKA_ENABLED:
        return False
    if not kafka_bus.broker_available():
        return False
    return kafka_bus.publish(file_id, file_name, file_path)
