"""Kafka 生产者与后台消费线程；Kafka 不可用时应用仍可正常启动。"""
import json
import logging
import threading
import time
from typing import Callable, Optional

from kafka import KafkaConsumer, KafkaProducer
from kafka.errors import KafkaError

from . import config

log = logging.getLogger(__name__)

_producer: Optional[KafkaProducer] = None


def get_producer() -> Optional[KafkaProducer]:
    global _producer
    if _producer is None:
        try:
            _producer = KafkaProducer(
                bootstrap_servers=config.KAFKA_BOOTSTRAP_SERVERS,
                value_serializer=lambda v: json.dumps(v, ensure_ascii=False).encode("utf-8"),
                max_block_ms=5000,
                retries=2,
            )
        except Exception as e:
            log.warning("Kafka 生产者创建失败: %s", e)
    return _producer


def send_parse_request(doc_id: str, pdf_path, filename: str) -> bool:
    """发送解析请求消息，返回是否发送成功。"""
    producer = get_producer()
    if producer is None:
        log.warning("Kafka 不可用，文档 %s 未进入解析队列", doc_id)
        return False
    try:
        producer.send(
            config.KAFKA_TOPIC,
            {"doc_id": doc_id, "pdf_path": str(pdf_path), "filename": filename},
        )
        producer.flush(timeout=10)
        return True
    except KafkaError as e:
        log.warning("Kafka 发送失败: %s", e)
        return False


def start_consumer(handler: Callable[[dict], None]) -> None:
    """后台线程消费解析请求，连接断开时自动重试。"""

    def run() -> None:
        while True:
            consumer = None
            try:
                consumer = KafkaConsumer(
                    config.KAFKA_TOPIC,
                    bootstrap_servers=config.KAFKA_BOOTSTRAP_SERVERS,
                    group_id=config.KAFKA_GROUP_ID,
                    auto_offset_reset="earliest",
                    value_deserializer=lambda v: json.loads(v.decode("utf-8")),
                    consumer_timeout_ms=1000,
                )
                log.info("Kafka 消费者已连接 %s", config.KAFKA_BOOTSTRAP_SERVERS)
                for msg in consumer:
                    if msg.value is None:
                        continue
                    handler(msg.value)
                consumer.close()
            except KafkaError as e:
                log.warning("Kafka 连接失败，5 秒后重试: %s", e)
                time.sleep(5)
            except Exception as e:
                log.exception("消费者异常，5 秒后重试: %s", e)
                time.sleep(5)
            finally:
                if consumer is not None:
                    consumer.close()

    threading.Thread(target=run, daemon=True, name="kafka-parse-worker").start()
