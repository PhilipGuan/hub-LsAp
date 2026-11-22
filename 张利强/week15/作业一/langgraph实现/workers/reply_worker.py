"""进程2：回复生成。

消费 reply-request → 按情绪策略调 Qwen 流式生成 → 逐 token 发布 reply-token。
最后一帧 is_final=True；失败时发 error 并由后端补兜底话术。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from openai import AsyncOpenAI

from backend.config import settings
from backend.kafka_bridge import json_deserializer, json_serializer
from backend.llm import LLMNotConfigured, create_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("reply-worker")

STRATEGY = {
    "positive": "用户当前情绪偏积极：先肯定、鼓励，再自然延展话题。",
    "negative": "用户当前情绪偏负面：先共情、接纳感受，再温和地帮忙疏解；不要急于给建议，不要说教。",
    "neutral": "用户情绪中性：自然回应，必要时用一句开放问题继续引导对话。",
}


def _system_prompt(emotion: dict[str, Any]) -> str:
    label = str(emotion.get("label") or "neutral")
    intensity = emotion.get("intensity") or "unknown"
    mixed = emotion.get("mixed_emotions") or []
    mixed_text = "、".join(str(item.get("label")) for item in mixed if isinstance(item, dict)) or "无"

    return (
        "你是一个有共情能力的中文对话助手，语气自然、真诚，不做心理诊断。\n"
        f"用户情绪标签：{label}；强度：{intensity}；细分情绪：{mixed_text}。\n"
        f"回复策略：{STRATEGY.get(label, STRATEGY['neutral'])}\n"
        "要求：2~4 句，口语化，不要说“作为一个 AI”，不要重复用户原话，不要输出列表或 Markdown。"
    )


def _build_messages(payload: dict[str, Any]) -> list[dict[str, str]]:
    emotion = payload.get("emotion_result") or {}
    messages: list[dict[str, str]] = [
        {"role": "system", "content": _system_prompt(emotion)}
    ]
    for item in (payload.get("history") or [])[-8:]:
        role = item.get("role")
        content = item.get("content")
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": str(content)})
    messages.append({"role": "user", "content": str(payload.get("user_input", ""))})
    return messages


async def _send_final(
    producer: AIOKafkaProducer, correlation_id: str, error: str | None = None
) -> None:
    await producer.send_and_wait(
        settings.topic_reply_token,
        {"correlation_id": correlation_id, "token": "", "is_final": True, "error": error},
    )


async def handle(
    client: AsyncOpenAI | None, producer: AIOKafkaProducer, payload: dict[str, Any]
) -> None:
    correlation_id = str(payload["correlation_id"])
    try:
        if client is None:
            raise RuntimeError("QWEN_API_KEY 未配置，回复生成不可用")

        completion = await client.chat.completions.create(
            model=settings.qwen_model,
            messages=_build_messages(payload),
            stream=True,
            temperature=0.7,
        )

        async for chunk in completion:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content
            if not delta:
                continue
            await producer.send(
                settings.topic_reply_token,
                {
                    "correlation_id": correlation_id,
                    "token": delta,
                    "is_final": False,
                    "error": None,
                },
            )

        await _send_final(producer, correlation_id)
    except Exception as exc:
        logger.exception("回复生成失败 correlation_id=%s", correlation_id)
        await _send_final(producer, correlation_id, error=str(exc))


async def main() -> None:
    consumer = AIOKafkaConsumer(
        settings.topic_reply_request,
        bootstrap_servers=settings.kafka_bootstrap,
        group_id=settings.reply_worker_group,
        value_deserializer=json_deserializer,
        auto_offset_reset="earliest",
    )
    producer = AIOKafkaProducer(
        bootstrap_servers=settings.kafka_bootstrap,
        value_serializer=json_serializer,
        acks="all",
    )

    await consumer.start()
    await producer.start()

    client: AsyncOpenAI | None = None
    try:
        client = create_client()
    except LLMNotConfigured as exc:
        logger.warning("%s；所有回复将失败，由后端返回兜底话术", exc)

    logger.info("回复生成 worker 已启动 topic=%s", settings.topic_reply_request)
    try:
        async for message in consumer:
            payload = message.value
            if not isinstance(payload, dict):
                logger.warning("忽略非 JSON 对象消息")
                continue
            if not payload.get("correlation_id"):
                logger.warning("忽略缺少 correlation_id 的消息")
                continue
            await handle(client, producer, payload)
    finally:
        await consumer.stop()
        await producer.stop()
        if client is not None:
            await client.close()
        logger.info("回复生成 worker 已停止")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
