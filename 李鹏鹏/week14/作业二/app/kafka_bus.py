"""Kafka 消息总线封装。

两条主题
--------
``rag.doc.parse``   上传成功后投递的「解析任务」消息（key = doc_id，保证同一文档有序）
``rag.doc.status``  解析工作进程上报的状态变更（pending/parsing/done/failed）

设计要点
--------
* 生产者懒加载：Kafka 挂了不会影响 Web 启动，只影响投递。
* 投递失败时（可选）退化为进程内直连队列，保证单机演示也能跑通；
  退化行为会写进文档的 ``source`` 字段，界面上能看出这条任务是
  ``kafka`` 还是 ``direct`` 通道来的。
* 消费者手动提交 offset：解析成功才提交，异常时标记失败后提交，避免毒消息死循环。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from .config import settings

logger = logging.getLogger(__name__)

TOPIC_PARSE = settings.kafka_parse_topic
TOPIC_STATUS = settings.kafka_status_topic

_lock = threading.Lock()
_producer: Any = None
_producer_error = ""
_producer_failed_at = 0.0
_direct_handler: Callable[[dict[str, Any]], None] | None = None
_health_cache: tuple[float, bool, str] = (0.0, False, "未检测")

#: Kafka 不可用时，最多每 15 秒重试一次，避免每个请求都卡在超时上
_RETRY_INTERVAL = 15.0


# --------------------------------------------------------------------------- #
# 序列化
# --------------------------------------------------------------------------- #
def _key_encode(key: object) -> bytes:
    return str(key).encode("utf-8")


def _value_encode(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def _key_decode(raw: bytes | None) -> str | None:
    return raw.decode("utf-8") if raw else None


def _value_decode(raw: bytes | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def envelope(event: str, **payload: Any) -> dict[str, Any]:
    return {"v": 1, "event": event, "ts": time.strftime("%Y-%m-%d %H:%M:%S"), **payload}


# --------------------------------------------------------------------------- #
# 健康检查 / topic 初始化
# --------------------------------------------------------------------------- #
def _admin(timeout_ms: int | None = None):
    from kafka import KafkaAdminClient

    return KafkaAdminClient(
        bootstrap_servers=settings.kafka_bootstrap,
        request_timeout_ms=timeout_ms or settings.kafka_client_timeout_ms,
        api_version_auto_timeout_ms=timeout_ms or settings.kafka_client_timeout_ms,
    )


def ensure_topics() -> tuple[bool, str]:
    """启动时幂等地建好两条主题；返回 (是否成功, 说明)。"""
    if not settings.kafka_enabled:
        return False, "Kafka 已通过 KAFKA_ENABLED=0 关闭"
    try:
        from kafka.admin import NewTopic

        admin = _admin()
        try:
            existing = set(admin.list_topics())
            missing = sorted({TOPIC_PARSE, TOPIC_STATUS} - existing)
            if missing and settings.kafka_auto_create:
                admin.create_topics(
                    [
                        NewTopic(
                            name=name,
                            num_partitions=settings.kafka_partitions,
                            replication_factor=settings.kafka_replicas,
                        )
                        for name in missing
                    ]
                )
            return True, f"已连接 {settings.kafka_bootstrap}"
        finally:
            admin.close()
    except Exception as exc:  # noqa: BLE001 - 需要把失败原因透出到 UI
        if type(exc).__name__ == "TopicAlreadyExistsError":  # 并发建 topic，视为成功
            return True, f"已连接 {settings.kafka_bootstrap}"
        logger.warning("Kafka 初始化失败: %s", exc)
        return False, f"{type(exc).__name__}: {exc}"


def kafka_health(force: bool = False) -> tuple[bool, str]:
    """带缓存（10s）的连通性探测，供 /api/health 使用。"""
    global _health_cache
    cached_at, ok, message = _health_cache
    if not force and time.time() - cached_at < 10:
        return ok, message
    if not settings.kafka_enabled:
        _health_cache = (time.time(), False, "Kafka 已关闭")
        return _health_cache[1], _health_cache[2]
    ok, message = ensure_topics()
    _health_cache = (time.time(), ok, message)
    return ok, message


# --------------------------------------------------------------------------- #
# 生产者
# --------------------------------------------------------------------------- #
def get_producer():
    """返回 KafkaProducer；短时间内失败过则直接抛错，避免请求全部挂住。"""
    global _producer, _producer_error, _producer_failed_at
    if _producer is not None:
        return _producer
    with _lock:
        if _producer is not None:
            return _producer
        if _producer_error and time.time() - _producer_failed_at < _RETRY_INTERVAL:
            raise RuntimeError(_producer_error)
        if not settings.kafka_enabled:
            raise RuntimeError("Kafka 已通过 KAFKA_ENABLED=0 关闭")
        try:
            from kafka import KafkaProducer

            producer = KafkaProducer(
                bootstrap_servers=settings.kafka_bootstrap,
                key_serializer=_key_encode,
                value_serializer=_value_encode,
                acks="all",
                retries=3,
                linger_ms=5,
                max_block_ms=settings.kafka_client_timeout_ms,
                request_timeout_ms=settings.kafka_client_timeout_ms,
                api_version_auto_timeout_ms=settings.kafka_client_timeout_ms,
            )
            _producer = producer
            _producer_error = ""
            logger.info("Kafka 生产者已连接 %s", settings.kafka_bootstrap)
            return producer
        except Exception as exc:  # noqa: BLE001
            _producer_error = f"{type(exc).__name__}: {exc}"
            _producer_failed_at = time.time()
            logger.warning("Kafka 生产者创建失败: %s", _producer_error)
            raise


def close_producer() -> None:
    global _producer
    with _lock:
        producer, _producer = _producer, None
        if producer is None:
            return
        try:
            producer.close(timeout=3)
        except Exception as exc:  # noqa: BLE001 - 关闭失败无需处理
            logger.debug("关闭 Kafka 生产者失败: %s", exc)


def _send(topic: str, key: str, payload: dict[str, Any], wait: bool = True) -> None:
    producer = get_producer()
    future = producer.send(topic, key=key, value=payload)
    if wait:
        future.get(timeout=settings.kafka_client_timeout_ms / 1000)


# --------------------------------------------------------------------------- #
# 直连兜底（Kafka 不可用时）
# --------------------------------------------------------------------------- #
def register_direct_handler(handler: Callable[[dict[str, Any]], None] | None) -> None:
    """注册进程内兜底消费函数；参数与 Kafka 消费到的消息体一致。"""
    global _direct_handler
    _direct_handler = handler


# --------------------------------------------------------------------------- #
# 对外 API
# --------------------------------------------------------------------------- #
def publish_parse_request(doc: dict[str, Any]) -> str:
    """投递解析任务，返回实际使用的通道：``kafka`` / ``direct``。"""
    payload = envelope(
        "doc.parse.requested",
        doc_id=doc["id"],
        filename=doc["filename"],
        stored_path=doc["stored_path"],
        size=doc.get("size", 0),
        attempt=1,
    )
    if settings.kafka_enabled:
        try:
            _send(TOPIC_PARSE, doc["id"], payload)
            return "kafka"
        except Exception as exc:  # noqa: BLE001
            logger.warning("投递解析任务到 Kafka 失败（%s），兜底走进程内队列", exc)
            if not settings.kafka_fallback_direct:
                raise
    if not settings.kafka_fallback_direct:
        raise RuntimeError("Kafka 不可用且未开启 KAFKA_FALLBACK_DIRECT")
    if _direct_handler is None:
        raise RuntimeError("Kafka 不可用，且没有注册进程内兜底消费者（worker 未启动）")
    _direct_handler(payload)
    return "direct"


def publish_status(
    doc_id: str,
    status: str,
    *,
    progress: float = 0.0,
    stage: str = "",
    detail: str = "",
    extra: dict[str, Any] | None = None,
) -> None:
    """上报状态事件；失败只记日志，绝不打断解析主流程。"""
    payload = envelope(
        "doc.parse.status",
        doc_id=doc_id,
        status=status,
        progress=round(_as_float(progress), 4),
        stage=stage,
        detail=detail,
        **(extra or {}),
    )
    if not settings.kafka_enabled:
        return
    try:
        _send(TOPIC_STATUS, doc_id, payload, wait=False)
    except Exception as exc:  # noqa: BLE001
        logger.debug("状态事件发送失败（忽略）: %s", exc)


def build_consumer(
    topics: list[str],
    group_id: str,
    *,
    max_poll_records: int = 1,
):
    """构造手动提交 offset 的消费者。"""
    from kafka import KafkaConsumer

    return KafkaConsumer(
        *topics,
        bootstrap_servers=settings.kafka_bootstrap,
        group_id=group_id,
        enable_auto_commit=False,
        auto_offset_reset="earliest",
        max_poll_records=max_poll_records,
        max_poll_interval_ms=max(300_000, settings.mineru_timeout * 1000),
        session_timeout_ms=30_000,
        request_timeout_ms=max(40_000, settings.kafka_client_timeout_ms),
        consumer_timeout_ms=-1,
        key_deserializer=_key_decode,
        value_deserializer=_value_decode,
    )


def describe() -> dict[str, Any]:
    return {
        "bootstrap": settings.kafka_bootstrap,
        "parse_topic": TOPIC_PARSE,
        "status_topic": TOPIC_STATUS,
        "group": settings.kafka_group,
        "fallback_direct": settings.kafka_fallback_direct,
    }
