import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from uuid import UUID

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer, TopicPartition

from backend.common.settings import Settings
from backend.model.schemas import ErrorResult

logger = logging.getLogger(__name__)


@asynccontextmanager
async def kafka_clients(settings: Settings, topic: str, group: str):
    producer = AIOKafkaProducer(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        acks="all", request_timeout_ms=10000,
    )
    consumer = AIOKafkaConsumer(
        topic, bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id=group, enable_auto_commit=False,
        auto_offset_reset="earliest",
        # Allow model processing, publishing and committing before the next poll.
        max_poll_interval_ms=max(300000, int((settings.llm_timeout_seconds + 60) * 1000)),
    )
    try:
        logger.info("Kafka 连接中 topic=%s group=%s", topic, group)
        await producer.start()
        await consumer.start()
        async with asyncio.timeout(30):
            while not consumer.assignment():
                await asyncio.sleep(0.05)
        logger.info("Kafka 就绪 topic=%s group=%s partitions=%s", topic, group, len(consumer.assignment()))
        yield producer, consumer
    except Exception as exc:
        logger.error("Kafka 会话失败 topic=%s group=%s error=%s", topic, group, type(exc).__name__)
        raise
    finally:
        try:
            await consumer.stop()
        finally:
            await producer.stop()
        logger.info("Kafka 连接已关闭 topic=%s group=%s", topic, group)


async def publish(producer, topic: str, message) -> None:
    logger.info("request_id=%s 开始发送 Kafka 消息 topic=%s", message.request_id, topic)
    try:
        await asyncio.wait_for(producer.send_and_wait(
            topic, value=message.model_dump_json().encode("utf-8"),
            key=str(message.request_id).encode("utf-8"),
        ), timeout=10)
    except Exception as exc:
        logger.error("request_id=%s Kafka 消息发送失败 topic=%s error=%s",
                     message.request_id, topic, type(exc).__name__)
        raise
    logger.info("request_id=%s Kafka 消息发送成功 topic=%s", message.request_id, topic)


async def commit_record(consumer, record) -> None:
    try:
        await consumer.commit({TopicPartition(record.topic, record.partition): record.offset + 1})
    except Exception as exc:
        logger.error("Kafka 位点提交失败 topic=%s partition=%s offset=%s error=%s",
                     record.topic, record.partition, record.offset + 1, type(exc).__name__)
        raise


async def process_record(record, producer, consumer, *, schema, process, stage, output_topic, settings):
    started = time.monotonic()
    request_id = None
    try:
        payload = json.loads(record.value)
        request_id = UUID(str(payload["request_id"]))
        message = schema.model_validate(payload)
    except (ValueError, TypeError, KeyError, UnicodeDecodeError):
        if request_id is None:
            logger.warning("stage=%s 跳过无法识别请求的消息 topic=%s partition=%s offset=%s",
                           stage, record.topic, record.partition, record.offset)
            await commit_record(consumer, record)
            return
        logger.warning("request_id=%s stage=%s 收到的 Kafka 消息格式校验失败 topic=%s partition=%s offset=%s",
                       request_id, stage, record.topic, record.partition, record.offset)
        result = ErrorResult(request_id=request_id, stage=stage,
                             code="INVALID_MESSAGE", message="消息格式错误，请重新提交。")
    else:
        logger.info("request_id=%s stage=%s 收到 Kafka 消息，开始处理 topic=%s partition=%s offset=%s",
                    request_id, stage, record.topic, record.partition, record.offset)
        try:
            async with asyncio.timeout(settings.llm_timeout_seconds + 10):
                result = await process(message)
        except Exception as exc:
            # Log the exception type only: SDK error text may contain credentials or input.
            logger.error("request_id=%s stage=%s 模型处理失败 error=%s elapsed=%.3fs",
                         request_id, stage, type(exc).__name__, time.monotonic() - started)
            result = ErrorResult(request_id=request_id, stage=stage,
                                 code="MODEL_CALL_FAILED", message="模型处理失败，请稍后重新提交。")
    destination = settings.kafka_response_topic if isinstance(result, ErrorResult) else output_topic
    # Publish errors propagate: never commit an input whose output was not acknowledged.
    await publish(producer, destination, result)
    await commit_record(consumer, record)
    logger.info("request_id=%s stage=%s 处理结束 status=%s elapsed=%.3fs",
                request_id, stage, "error" if isinstance(result, ErrorResult) else "success",
                time.monotonic() - started)


async def run_worker(settings, *, input_topic, group, output_topic, schema, process, stage):
    logger.info("Agent 消费进程启动 stage=%s input_topic=%s output_topic=%s group=%s",
                stage, input_topic, output_topic, group)
    try:
        async with kafka_clients(settings, input_topic, group) as (producer, consumer):
            async for record in consumer:
                await process_record(record, producer, consumer, schema=schema, process=process,
                                     stage=stage, output_topic=output_topic, settings=settings)
    finally:
        logger.info("Agent 消费进程已停止 stage=%s", stage)
