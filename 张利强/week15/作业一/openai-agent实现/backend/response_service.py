"""进程 2：澄清路由 + 鼓励/排解 + 流式发布。

消费 sentiment.result：
- 置信度低且轮次未耗尽 -> 澄清 Agent，一次性下发问题；
- 否则 -> 流式运行鼓励/排解 Agent，token 经 Redis Pub/Sub 旁路推送；
- 最终结果 produce 到 chat.response（审计 / 幂等 / 重放）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import time

import redis.asyncio as aioredis
from agents import Agent, RunContextWrapper, Runner, trace
from agents.extensions.memory import RedisSession
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer, TopicPartition
from openai.types.responses import ResponseTextDeltaEvent
from pydantic import ValidationError

from backend.config import (
    CONFIDENCE_THRESHOLD,
    KAFKA_BOOTSTRAP,
    LOG_LEVEL,
    MAX_CLARIFICATION_ROUNDS,
    QWEN_MODEL,
    REDIS_URL,
    SESSION_TTL_SECONDS,
    require_api_key,
)
from backend.contracts import (
    TOPIC_CHAT_RESPONSE,
    TOPIC_CHAT_RESPONSE_DLQ,
    TOPIC_SENTIMENT_RESULT,
    ChatResponse,
    SentimentResult,
)
from backend.llm import get_model

GROUP_ID = "response-svc"
RETRY_ATTEMPTS = 3
SESSION_KEY_PREFIX = "support:session"

log = logging.getLogger("response")

BASE_RESPONSE_INSTRUCTIONS = (
    "你是“暖语”，一位温和、可靠的情感支持者，正在和用户进行多轮对话。\n"
    "你的目标是让用户感到被理解、被陪伴，而不是被教育。\n"
    "要求：\n"
    "1. 先接住情绪，再给出陪伴或小小建议；不评判、不说教、不轻易下结论。\n"
    "2. 语气自然口语化，像朋友聊天；避免“作为一个 AI”“根据你的描述”等机械表达。\n"
    "3. 不承诺“一定会好起来”，不提供医疗、用药或诊断建议。\n"
    "4. 如果用户流露出自伤、自杀或伤害他人的念头，认真对待，鼓励其立即联系身边信任的人或当地心理援助热线。\n"
    "5. 回复控制在 120 字以内，不使用列表、标题或表情符号。"
)

BASE_CLARIFICATION_INSTRUCTIONS = (
    "你是情感支持对话中的澄清助手。\n"
    "当系统无法确定用户当前情绪时，你需要提出一个具体、温和、开放式的追问，帮助用户补充信息。\n"
    "要求：\n"
    "1. 只输出这一句问题，最多 40 字；\n"
    "2. 不要重复用户原话，不要急着安慰，不要一次问多个问题；\n"
    "3. 语气自然，像朋友轻轻问一句。"
)


def _response_instructions(
    ctx: RunContextWrapper[SentimentResult],
    agent: Agent[SentimentResult],
) -> str:
    msg = ctx.context
    label = "偏积极" if msg.sentiment == "positive" else "偏消极"
    hint = ""
    if msg.clarification_round > 0:
        hint = "用户刚刚回答过你的澄清追问，请先自然承接，再展开回应。"
    return (
        f"{BASE_RESPONSE_INSTRUCTIONS}\n\n"
        f"【本轮参考】用户情绪判定：{label}（置信度 {msg.confidence:.2f}）。{hint}"
    )


def _clarification_instructions(
    ctx: RunContextWrapper[SentimentResult],
    agent: Agent[SentimentResult],
) -> str:
    msg = ctx.context
    return (
        f"{BASE_CLARIFICATION_INSTRUCTIONS}\n\n"
        f"【本轮参考】情绪判定置信度仅 {msg.confidence:.2f}，需要追问。"
    )


RESPONSE_AGENT = Agent(
    name="ResponseAgent",
    model=get_model(),
    instructions=_response_instructions,
)

CLARIFICATION_AGENT = Agent(
    name="ClarificationAgent",
    model=get_model(),
    instructions=_clarification_instructions,
)


def claim_key(request_id: str) -> str:
    return f"processed:{request_id}"


def cancel_key(request_id: str) -> str:
    return f"cancel:{request_id}"


async def claim_request(redis_client: aioredis.Redis, request_id: str) -> bool:
    """原子认领，防止崩溃重投递导致重复回复。"""
    return (
        await redis_client.set(claim_key(request_id), "1", nx=True, ex=600)
    ) is not None


async def publish(
    redis_client: aioredis.Redis,
    request_id: str,
    payload: dict,
) -> None:
    await redis_client.publish(
        f"stream:{request_id}",
        json.dumps(payload, ensure_ascii=False),
    )


async def commit_record(consumer: AIOKafkaConsumer, record) -> None:
    tp = TopicPartition(record.topic, record.partition)
    await consumer.commit({tp: record.offset + 1})


async def send_dlq(producer: AIOKafkaProducer, record, error: Exception) -> bool:
    try:
        await producer.send_and_wait(
            TOPIC_CHAT_RESPONSE_DLQ,
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
    except Exception:  # noqa: BLE001
        log.exception("failed to write DLQ for offset=%s", record.offset)
        return False


def need_clarification(msg: SentimentResult) -> bool:
    return (
        msg.confidence < CONFIDENCE_THRESHOLD
        and msg.clarification_round < MAX_CLARIFICATION_ROUNDS
    )


async def run_with_retry(factory):
    last_exc: Exception | None = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            return await factory()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            log.warning("generation failed (attempt %s/%s): %s", attempt, RETRY_ATTEMPTS, exc)
            if attempt < RETRY_ATTEMPTS:
                await asyncio.sleep(2 ** (attempt - 1))
    assert last_exc is not None
    raise last_exc


async def generate_clarification(msg: SentimentResult) -> str:
    result = await Runner.run(
        CLARIFICATION_AGENT,
        msg.message,
        context=msg,
        max_turns=2,
    )
    reply = result.final_output
    if not isinstance(reply, str) or not reply.strip():
        raise RuntimeError("empty clarification reply")
    return reply.strip()


async def stream_final_reply(
    msg: SentimentResult,
    redis_client: aioredis.Redis,
) -> ChatResponse | None:
    """流式生成最终回复；返回 None 表示客户端已取消。

    首个 token 之前失败可以安全重试；一旦已经推过 token 就不再重试，
    否则用户会看到重复内容。
    """
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        state = {"streamed": False}
        try:
            return await _stream_once(msg, redis_client, state)
        except Exception as exc:  # noqa: BLE001
            if state["streamed"] or attempt >= RETRY_ATTEMPTS:
                raise
            log.warning(
                "streaming failed before first token (attempt %s/%s) request_id=%s: %s",
                attempt,
                RETRY_ATTEMPTS,
                msg.request_id,
                exc,
            )
            await asyncio.sleep(2 ** (attempt - 1))

    raise RuntimeError("unreachable")


async def _stream_once(
    msg: SentimentResult,
    redis_client: aioredis.Redis,
    state: dict,
) -> ChatResponse | None:
    """单次流式尝试；state["streamed"] 标记是否已经向前端推过 token。"""
    session = RedisSession(
        session_id=msg.session_id,
        redis_client=redis_client,
        ttl=SESSION_TTL_SECONDS,
        key_prefix=SESSION_KEY_PREFIX,
    )
    result = Runner.run_streamed(
        RESPONSE_AGENT,
        msg.message,
        context=msg,
        session=session,
        max_turns=3,
    )

    chunks: list[str] = []
    cancelled = False
    last_cancel_check = 0.0

    # 必须完整消费迭代器，提前 break 会破坏会话持久化。
    async for event in result.stream_events():
        if event.type != "raw_response_event":
            continue
        if not isinstance(event.data, ResponseTextDeltaEvent):
            continue
        delta = event.data.delta
        if not delta:
            continue

        chunks.append(delta)

        if cancelled:
            continue

        await publish(redis_client, msg.request_id, {"type": "token", "token": delta})
        state["streamed"] = True

        now = time.monotonic()
        if now - last_cancel_check >= 1.0:
            last_cancel_check = now
            if await redis_client.exists(cancel_key(msg.request_id)):
                log.info("client cancelled request_id=%s", msg.request_id)
                cancelled = True
                result.cancel()

    if cancelled:
        return None

    reply = result.final_output
    if not isinstance(reply, str) or not reply.strip():
        reply = "".join(chunks).strip()
    if not reply:
        raise RuntimeError("empty final reply")

    await publish(redis_client, msg.request_id, {"done": True, "full": reply})
    return ChatResponse(
        request_id=msg.request_id,
        session_id=msg.session_id,
        type="final",
        reply=reply,
        sentiment=msg.sentiment,
        confidence=msg.confidence,
    )


async def handle_record(
    record,
    consumer: AIOKafkaConsumer,
    producer: AIOKafkaProducer,
    redis_client: aioredis.Redis,
) -> None:
    try:
        msg = SentimentResult.model_validate_json(record.value)
    except ValidationError as exc:
        log.error("invalid sentiment.result at offset=%s: %s", record.offset, exc)
        if await send_dlq(producer, record, exc):
            await commit_record(consumer, record)
        return

    if not await claim_request(redis_client, msg.request_id):
        log.info("duplicate sentiment.result skipped request_id=%s", msg.request_id)
        await commit_record(consumer, record)
        return

    try:
        with trace(
            workflow_name="support-pipeline",
            group_id=msg.session_id,
            metadata={
                "request_id": msg.request_id,
                "stage": "response",
                "confidence": msg.confidence,
            },
        ):
            if need_clarification(msg):
                reply = await run_with_retry(lambda: generate_clarification(msg))
                await publish(
                    redis_client,
                    msg.request_id,
                    {"type": "clarification", "reply": reply},
                )
                response = ChatResponse(
                    request_id=msg.request_id,
                    session_id=msg.session_id,
                    type="clarification",
                    reply=reply,
                    sentiment=msg.sentiment,
                    confidence=msg.confidence,
                )
            else:
                response = await stream_final_reply(msg, redis_client)
                if response is None:
                    await commit_record(consumer, record)
                    return

        await producer.send_and_wait(
            TOPIC_CHAT_RESPONSE,
            response.to_kafka(),
            key=msg.session_id.encode("utf-8"),
        )
        await commit_record(consumer, record)
        log.info(
            "response done request_id=%s type=%s",
            response.request_id,
            response.type,
        )
    except Exception as exc:  # noqa: BLE001
        await publish(redis_client, msg.request_id, {"error": "generation_failed"})
        if await send_dlq(producer, record, exc):
            # 释放认领，保证修复后从 DLQ 重放时不会被幂等键直接跳过。
            await redis_client.delete(claim_key(msg.request_id))
            await commit_record(consumer, record)
        else:
            # 没能进入 DLQ 时不提交，并释放认领，允许重投递重新处理。
            await redis_client.delete(claim_key(msg.request_id))


async def consume_loop(
    consumer: AIOKafkaConsumer,
    producer: AIOKafkaProducer,
    redis_client: aioredis.Redis,
    stop: asyncio.Event,
) -> None:
    while not stop.is_set():
        try:
            batches = await consumer.getmany(timeout_ms=500, max_records=10)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("failed to fetch records, retrying")
            await asyncio.sleep(1)
            continue

        for records in batches.values():
            for record in records:
                if stop.is_set():
                    return
                try:
                    await handle_record(record, consumer, producer, redis_client)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - 单条消息异常不应终止消费循环
                    log.exception("unhandled error at offset=%s", record.offset)


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
        except NotImplementedError:  # pragma: no cover
            pass

    redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
    consumer = AIOKafkaConsumer(
        TOPIC_SENTIMENT_RESULT,
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
        "response service started (model=%s), consuming %s",
        QWEN_MODEL,
        TOPIC_SENTIMENT_RESULT,
    )
    try:
        await consume_loop(consumer, producer, redis_client, stop)
    finally:
        log.info("stopping response service")
        await consumer.stop()
        await producer.stop()
        await redis_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
