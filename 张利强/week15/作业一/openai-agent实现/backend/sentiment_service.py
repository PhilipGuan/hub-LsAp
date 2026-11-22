"""进程 1：情感识别。

消费 chat.request -> 调用情感识别 Agent -> 产出 sentiment.result。
- 不传 Session：情绪判断只看当前这句话。
- 手动提交 offset：sentiment.result 发送成功后才 commit。
- 失败重试 3 次后进入 sentiment.result.dlq。
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
from typing import Literal

from agents import Agent, ModelSettings, Runner, trace
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer, TopicPartition
from pydantic import BaseModel, Field, ValidationError

from backend.config import KAFKA_BOOTSTRAP, LOG_LEVEL, QWEN_MODEL, require_api_key
from backend.contracts import (
    TOPIC_CHAT_REQUEST,
    TOPIC_SENTIMENT_RESULT,
    TOPIC_SENTIMENT_RESULT_DLQ,
    ChatRequest,
    SentimentResult,
)
from backend.llm import get_model

RETRY_ATTEMPTS = 3
GROUP_ID = "sentiment-svc"

log = logging.getLogger("sentiment")


class SentimentJudgement(BaseModel):
    """LLM 的结构化输出，只包含模型真正能判断的字段。"""

    sentiment: Literal["positive", "negative"] = Field(
        description="positive 表示积极情绪，negative 表示消极情绪",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="对本次判断的把握程度，0 表示完全不确定，1 表示非常确定",
    )


SENTIMENT_AGENT = Agent(
    name="SentimentAgent",
    model=get_model(),
    model_settings=ModelSettings(
        # Qwen 的 JSON 模式：保证返回合法 JSON，再由 Pydantic 严格校验字段。
        extra_body={"response_format": {"type": "json_object"}},
    ),
    instructions=(
        "你是一名情感识别器，只判断用户当前这句话的整体情绪倾向。\n"
        "- positive：轻松、愉快、满意、期待、感激、平静等；\n"
        "- negative：焦虑、沮丧、愤怒、孤独、疲惫、无助等。\n"
        "准确输出 sentiment 与 confidence；confidence 是你对判断的把握，不是情绪强度。\n"
        "用户可能使用反讽或轻描淡写，请结合字面含义判断其主要情绪。\n"
        "只输出一个 JSON 对象，不要解释，不要 Markdown 代码块。格式：\n"
        '{"sentiment": "positive" 或 "negative", "confidence": 0 到 1 之间的小数}'
    ),
)


def parse_judgement(raw: str) -> SentimentJudgement:
    """解析并校验 Qwen 返回的 JSON，兼容偶发的 ``` 包裹或前后说明文字。"""
    if not isinstance(raw, str):
        raise TypeError(f"unexpected sentiment output: {type(raw)!r}")

    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].lstrip()

    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError(f"sentiment output is not JSON: {raw!r}")

    data = json.loads(text[start : end + 1])
    return SentimentJudgement.model_validate(data)


async def analyze(msg: ChatRequest) -> SentimentResult:
    """调用 Agent 得到结构化情绪判断，并拼装为下游消息。"""
    with trace(
        workflow_name="support-pipeline",
        group_id=msg.session_id,
        metadata={"request_id": msg.request_id, "stage": "sentiment"},
    ):
        result = await Runner.run(SENTIMENT_AGENT, msg.message, max_turns=3)

    judgement = parse_judgement(result.final_output)

    return SentimentResult(
        request_id=msg.request_id,
        session_id=msg.session_id,
        message=msg.message,
        sentiment=judgement.sentiment,
        confidence=judgement.confidence,
        clarification_round=msg.clarification_round,
    )


async def run_with_retry(msg: ChatRequest) -> SentimentResult:
    last_exc: Exception | None = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            return await analyze(msg)
        except Exception as exc:  # noqa: BLE001 - 任何失败都走重试 / DLQ
            last_exc = exc
            log.warning(
                "sentiment failed (attempt %s/%s) request_id=%s: %s",
                attempt,
                RETRY_ATTEMPTS,
                msg.request_id,
                exc,
            )
            if attempt < RETRY_ATTEMPTS:
                await asyncio.sleep(2 ** (attempt - 1))
    assert last_exc is not None
    raise last_exc


async def commit_record(consumer: AIOKafkaConsumer, record) -> None:
    """只提交当前这条消息的 offset，避免提前提交同批次未处理的消息。"""
    tp = TopicPartition(record.topic, record.partition)
    await consumer.commit({tp: record.offset + 1})


async def send_dlq(producer: AIOKafkaProducer, record, error: Exception) -> bool:
    try:
        await producer.send_and_wait(
            TOPIC_SENTIMENT_RESULT_DLQ,
            record.value,
            key=record.key,
        )
        log.error(
            "request moved to DLQ topic=%s partition=%s offset=%s: %s",
            record.topic,
            record.partition,
            record.offset,
            error,
        )
        return True
    except Exception:  # noqa: BLE001 - DLQ 写入失败时不能提交，留给下次重投
        log.exception("failed to write DLQ for offset=%s", record.offset)
        return False


async def handle_record(
    record,
    consumer: AIOKafkaConsumer,
    producer: AIOKafkaProducer,
) -> None:
    try:
        msg = ChatRequest.model_validate_json(record.value)
    except ValidationError as exc:
        log.error("invalid chat.request at offset=%s: %s", record.offset, exc)
        if await send_dlq(producer, record, exc):
            await commit_record(consumer, record)
        return

    try:
        result = await run_with_retry(msg)
    except Exception as exc:  # noqa: BLE001
        if await send_dlq(producer, record, exc):
            await commit_record(consumer, record)
        return

    await producer.send_and_wait(
        TOPIC_SENTIMENT_RESULT,
        result.to_kafka(),
        key=msg.session_id.encode("utf-8"),
    )
    await commit_record(consumer, record)
    log.info(
        "sentiment done request_id=%s sentiment=%s confidence=%.2f",
        result.request_id,
        result.sentiment,
        result.confidence,
    )


async def consume_loop(
    consumer: AIOKafkaConsumer,
    producer: AIOKafkaProducer,
    stop: asyncio.Event,
) -> None:
    while not stop.is_set():
        try:
            batches = await consumer.getmany(timeout_ms=500, max_records=10)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - broker 抖动时重连，不退出进程
            log.exception("failed to fetch records, retrying")
            await asyncio.sleep(1)
            continue

        for records in batches.values():
            for record in records:
                if stop.is_set():
                    return
                await handle_record(record, consumer, producer)


async def main() -> None:
    logging.basicConfig(
        level=LOG_LEVEL,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    require_api_key()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # pragma: no cover - 非 POSIX 平台
            pass

    consumer = AIOKafkaConsumer(
        TOPIC_CHAT_REQUEST,
        bootstrap_servers=KAFKA_BOOTSTRAP,
        group_id=GROUP_ID,
        enable_auto_commit=False,
        auto_offset_reset="earliest",
    )
    producer = AIOKafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP,
        acks="all",
        enable_idempotence=True,
    )

    await consumer.start()
    await producer.start()
    log.info(
        "sentiment service started (model=%s), consuming %s",
        QWEN_MODEL,
        TOPIC_CHAT_REQUEST,
    )
    try:
        await consume_loop(consumer, producer, stop)
    finally:
        log.info("stopping sentiment service")
        await consumer.stop()
        await producer.stop()


if __name__ == "__main__":
    asyncio.run(main())
