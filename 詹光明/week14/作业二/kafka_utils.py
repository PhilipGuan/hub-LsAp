"""
Kafka 生产 / 消费封装。

- 上传端（Streamlit）：把“待解析文件”投递到 KAFKA_TOPIC
- 解析端（worker）：消费同一条消息，慢慢调用 MinerU 解析

消息体：
    {"id": 文件主键, "file_name": "xxx.pdf", "file_path": "/abs/path.pdf"}
"""
import json
import logging

from config import KAFKA_BOOTSTRAP_SERVERS, KAFKA_GROUP_ID, KAFKA_TOPIC, PARSE_TIMEOUT

logger = logging.getLogger(__name__)

_producer = None


def _filter_supported(config: dict, default_config: dict, label: str) -> dict:
    """
    不同 kafka-python 版本支持的配置项不完全一致。
    这里按当前版本实际支持的配置过滤，避免因为某个可选参数直接报
    "Unrecognized configs"。
    """
    supported = set(default_config)
    unknown = sorted(set(config) - supported)
    if unknown:
        logger.warning(
            "当前 kafka-python 不支持 %s 配置项，已忽略: %s",
            label,
            ", ".join(unknown),
        )
    return {key: value for key, value in config.items() if key in supported}


def get_producer():
    """惰性创建全局 producer（进程内复用，线程安全由 kafka-python 保证）。"""
    global _producer
    if _producer is None:
        from kafka import KafkaProducer

        config = {
            "bootstrap_servers": KAFKA_BOOTSTRAP_SERVERS,
            "value_serializer": lambda v: json.dumps(v, ensure_ascii=False).encode("utf-8"),
            "key_serializer": lambda k: str(k).encode("utf-8") if k is not None else None,
            "acks": "all",          # 等待副本确认，尽量不丢消息
            "retries": 3,
            "linger_ms": 10,
            # broker 不可用时尽快失败，避免前端卡住
            "request_timeout_ms": 5000,
            "max_block_ms": 5000,
        }
        config = _filter_supported(config, KafkaProducer.DEFAULT_CONFIG, "producer")
        _producer = KafkaProducer(**config)
        logger.info("Kafka producer 已连接 %s", KAFKA_BOOTSTRAP_SERVERS)
    return _producer


def publish_parse_task(file_id: int, filepath: str, filename: str):
    """投递一条解析任务，返回 Kafka 的元信息；失败时抛异常由调用方处理。"""
    producer = get_producer()
    future = producer.send(
        KAFKA_TOPIC,
        key=str(file_id),
        value={"id": int(file_id), "file_name": filename, "file_path": str(filepath)},
    )
    metadata = future.get(timeout=10)
    producer.flush()
    logger.info(
        "已投递解析任务 file_id=%s -> topic=%s partition=%s offset=%s",
        file_id,
        metadata.topic,
        metadata.partition,
        metadata.offset,
    )
    return metadata


def get_consumer():
    """worker 使用的消费者：单条拉取 + 手动提交 offset。"""
    from kafka import KafkaConsumer

    config = {
        "bootstrap_servers": KAFKA_BOOTSTRAP_SERVERS,
        "group_id": KAFKA_GROUP_ID,
        "auto_offset_reset": "earliest",   # 没有 offset 时从头消费
        "enable_auto_commit": False,       # 处理完（无论成功失败）再提交
        "max_poll_records": 1,             # 一次一个文件，避免长时间不 poll 被踢出组
        # MinerU 可能解析很久，必须把 max.poll.interval.ms 放大，
        # 否则解析期间消费者会被踢出消费组，导致重复消费
        "max_poll_interval_ms": (PARSE_TIMEOUT + 120) * 1000,
        "value_deserializer": lambda v: json.loads(v.decode("utf-8")),
        "key_deserializer": lambda k: k.decode("utf-8") if k else None,
    }
    config = _filter_supported(config, KafkaConsumer.DEFAULT_CONFIG, "consumer")
    return KafkaConsumer(KAFKA_TOPIC, **config)
