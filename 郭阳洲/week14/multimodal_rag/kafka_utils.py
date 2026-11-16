import json
from kafka import KafkaProducer, KafkaConsumer
import config

_producer = None


def get_producer():
    global _producer
    if _producer is None:
        _producer = KafkaProducer(
            bootstrap_servers=config.KAFKA_BOOTSTRAP,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        )
    return _producer


def publish_task(doc_id, filename, filepath):
    """把一次 PDF 解析任务投递到 Kafka"""
    msg = {"doc_id": doc_id, "filename": filename, "filepath": filepath}
    get_producer().send(config.KAFKA_TOPIC, msg)
    get_producer().flush()


def consume_tasks():
    """返回一个消费解析任务的 KafkaConsumer"""
    return KafkaConsumer(
        config.KAFKA_TOPIC,
        bootstrap_servers=config.KAFKA_BOOTSTRAP,
        value_deserializer=lambda v: json.loads(v.decode("utf-8")),
        group_id="pdf-parser-worker",
        auto_offset_reset="earliest",
        enable_auto_commit=True,
    )