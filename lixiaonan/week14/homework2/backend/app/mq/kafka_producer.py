"""Kafka Producer 封装（功能1 / F1-5）。

- 发送成功才视为上传成功（acks=all + 等待 ack）
- 发送失败抛 KafkaSendError，由上层把任务标记为 FAILED（F1-5）
- 连接为懒初始化：API 启动不因 Kafka 暂不可用而失败，首次发送时建立连接
"""
import json
import threading

from kafka import KafkaProducer
from loguru import logger

from app.core.config import Settings
from app.mq.message_schemas import ParseTaskMessage


class KafkaSendError(Exception):
    """Kafka 消息发送失败（连接不可用 / 超时等）。"""


class MQProducer:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._producer: KafkaProducer | None = None
        self._lock = threading.Lock()

    def _ensure(self) -> KafkaProducer:
        if self._producer is None:
            with self._lock:
                if self._producer is None:
                    self._producer = KafkaProducer(
                        bootstrap_servers=self.settings.kafka_bootstrap_servers,
                        acks="all",
                        retries=5,
                        value_serializer=lambda v: json.dumps(v, ensure_ascii=False).encode("utf-8"),
                        key_serializer=lambda k: k.encode("utf-8") if k else None,
                    )
        return self._producer

    def send_parse_task(self, msg: ParseTaskMessage, key: str | None = None) -> None:
        """发送解析任务消息并等待 broker ack；失败统一抛 KafkaSendError。"""
        try:
            producer = self._ensure()
            future = producer.send(
                self.settings.kafka_topic_parse_task,
                key=key or msg.file_id,
                value=msg.model_dump(mode="json"),
            )
            meta = future.get(timeout=10)
            logger.info(
                f"Kafka 消息已发送 topic={self.settings.kafka_topic_parse_task} "
                f"task_id={msg.task_id} file_id={msg.file_id} "
                f"partition={meta.partition} offset={meta.offset} retry_count={msg.retry_count}"
            )
        except Exception as exc:  # NoBrokersAvailable / TimeoutError / KafkaError 等
            raise KafkaSendError(f"Kafka 消息发送失败: {exc}") from exc

    def ping(self) -> bool:
        """连接探活（健康检查用），成功后连接复用。"""
        try:
            self._ensure()
            return True
        except Exception:
            return False

    def close(self) -> None:
        if self._producer is not None:
            try:
                self._producer.flush(timeout=5)
                self._producer.close(timeout=5)
            except Exception:
                pass
            self._producer = None
