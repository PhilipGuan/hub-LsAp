"""进程1：情感识别。

消费 emotion-request → 调 Qwen（内部 stream=True 聚合完整结果）→ 发布 emotion-result。
失败时只发布 error 字段，由后端降级为 neutral。
"""

from __future__ import annotations

import asyncio
import json
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
logger = logging.getLogger("emotion-worker")

LABELS = {"positive", "negative", "neutral"}
INTENSITIES = {"low", "medium", "high"}

SYSTEM_PROMPT = """你是情感识别器。分析用户最后一句话的情感，只输出一个 JSON 对象，
不要输出解释、Markdown 或代码块标记。格式：
{"label":"positive|negative|neutral","confidence":0.0,"intensity":"low|medium|high","mixed_emotions":[{"label":"frustration","score":0.7}]}
要求：
- label 只能是 positive / negative / neutral 三者之一；
- confidence 是 0~1 的小数；
- intensity 表示情绪强度：low / medium / high；
- mixed_emotions 是细分情绪列表（可为空数组），score 为 0~1。"""


def _extract_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned[3:]
        if cleaned[:4].lower() == "json":
            cleaned = cleaned[4:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end <= start:
        raise ValueError(f"模型输出不含 JSON 对象: {text[:120]!r}")

    data = json.loads(cleaned[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("模型输出 JSON 不是对象")
    return data


def _clamp01(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return None


def _normalize(data: dict[str, Any]) -> dict[str, Any]:
    label = str(data.get("label") or "neutral").lower()
    if label not in LABELS:
        label = "neutral"

    intensity = data.get("intensity")
    if intensity not in INTENSITIES:
        intensity = None

    mixed_raw = data.get("mixed_emotions")
    mixed: list[dict[str, Any]] | None = None
    if isinstance(mixed_raw, list):
        mixed = []
        for item in mixed_raw:
            if not isinstance(item, dict):
                continue
            score = _clamp01(item.get("score"))
            mixed.append({"label": str(item.get("label", "unknown")), "score": score or 0.0})

    return {
        "label": label,
        "confidence": _clamp01(data.get("confidence")),
        "intensity": intensity,
        "mixed_emotions": mixed,
    }


async def classify(
    client: AsyncOpenAI, user_input: str, history: list[dict[str, Any]]
) -> dict[str, Any]:
    messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for item in history[-6:]:
        role = item.get("role")
        content = item.get("content")
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": str(content)})
    messages.append({"role": "user", "content": user_input})

    completion = await client.chat.completions.create(
        model=settings.qwen_model,
        messages=messages,
        stream=True,
        temperature=0.0,
    )

    chunks: list[str] = []
    async for chunk in completion:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta.content
        if delta:
            chunks.append(delta)

    return _normalize(_extract_json("".join(chunks)))


async def handle(client: AsyncOpenAI | None, data: dict[str, Any]) -> dict[str, Any]:
    correlation_id = data["correlation_id"]
    try:
        if client is None:
            raise RuntimeError("QWEN_API_KEY 未配置，情感识别不可用")
        result = await classify(
            client, str(data.get("user_input", "")), list(data.get("history") or [])
        )
        return {"correlation_id": correlation_id, **result, "error": None}
    except Exception as exc:
        logger.exception("情感识别失败 correlation_id=%s", correlation_id)
        return {
            "correlation_id": correlation_id,
            "label": "neutral",
            "confidence": None,
            "intensity": None,
            "mixed_emotions": None,
            "error": str(exc),
        }


async def main() -> None:
    consumer = AIOKafkaConsumer(
        settings.topic_emotion_request,
        bootstrap_servers=settings.kafka_bootstrap,
        group_id=settings.emotion_worker_group,
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
        logger.warning("%s；所有情感识别将返回 error，由后端降级", exc)

    logger.info("情感识别 worker 已启动 topic=%s", settings.topic_emotion_request)
    try:
        async for message in consumer:
            data = message.value
            if not isinstance(data, dict):
                logger.warning("忽略非 JSON 对象消息")
                continue
            if not data.get("correlation_id"):
                logger.warning("忽略缺少 correlation_id 的消息")
                continue
            await producer.send_and_wait(
                settings.topic_emotion_result, await handle(client, data)
            )
    finally:
        await consumer.stop()
        await producer.stop()
        if client is not None:
            await client.close()
        logger.info("情感识别 worker 已停止")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
