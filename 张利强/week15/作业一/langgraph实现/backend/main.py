"""FastAPI 入口：lifespan 装配 KafkaBridge + LangGraph checkpointer，/chat 走 SSE。"""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from pydantic import BaseModel, Field

from .config import settings
from .graph import build_graph
from .kafka_bridge import KafkaBridge, register_pending, unregister_pending
from .sse import SSE_HEADERS, sse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("emotion-chat")

FALLBACK_REPLY = "我暂时没能生成回复，但我在听，你可以再多说一点吗？"
LABELS = {"positive", "negative", "neutral"}
INTENSITIES = {"low", "medium", "high"}


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    user_id: str = Field(default="u1", min_length=1, max_length=64)
    conversation_id: str = Field(default="c1", min_length=1, max_length=64)


def _neutral_emotion() -> dict[str, Any]:
    return {"label": "neutral", "confidence": None, "intensity": None, "mixed_emotions": None}


def _normalize_emotion(raw: dict[str, Any]) -> dict[str, Any]:
    label = str(raw.get("label") or "neutral").lower()
    if label not in LABELS:
        label = "neutral"

    confidence = raw.get("confidence")
    if confidence is not None:
        try:
            confidence = max(0.0, min(1.0, float(confidence)))
        except (TypeError, ValueError):
            confidence = None

    intensity = raw.get("intensity")
    if intensity not in INTENSITIES:
        intensity = None

    mixed = raw.get("mixed_emotions")
    if not isinstance(mixed, list):
        mixed = None

    return {
        "label": label,
        "confidence": confidence,
        "intensity": intensity,
        "mixed_emotions": mixed,
    }


async def _load_history(graph, thread_id: str) -> list[dict[str, Any]]:
    try:
        snapshot = await graph.aget_state({"configurable": {"thread_id": thread_id}})
    except Exception:
        logger.exception("读取历史状态失败 thread_id=%s", thread_id)
        return []
    values = getattr(snapshot, "values", None) or {}
    history = values.get("history") or []
    if not isinstance(history, list):
        return []
    return history[-settings.history_limit :]


async def _record_turn(
    graph, thread_id: str, user_input: str, emotion: dict[str, Any], reply: str
) -> None:
    """每回合只写一次；写入失败不影响已返回给用户的回复。"""
    try:
        await graph.ainvoke(
            {
                "user_input": user_input,
                "emotion_label": emotion["label"],
                "confidence": emotion["confidence"],
                "intensity": emotion["intensity"],
                "mixed_emotions": emotion["mixed_emotions"],
                "reply": reply,
            },
            config={"configurable": {"thread_id": thread_id}},
        )
    except Exception:
        logger.exception("写入回合状态失败 thread_id=%s", thread_id)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_sqlite_dir()
    bridge = KafkaBridge()
    await bridge.start()
    logger.info("Kafka 已连接 bootstrap=%s", settings.kafka_bootstrap)

    async with AsyncSqliteSaver.from_conn_string(str(settings.sqlite_file)) as saver:
        app.state.bridge = bridge
        app.state.graph = build_graph(saver)
        logger.info("服务已启动 sqlite=%s", settings.sqlite_file)
        try:
            yield
        finally:
            await bridge.stop()
            logger.info("服务已停止")


app = FastAPI(title="Emotion Chat", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


async def _chat_stream(request: Request, payload: ChatRequest) -> AsyncIterator[str]:
    bridge: KafkaBridge = request.app.state.bridge
    graph = request.app.state.graph

    correlation_id = uuid.uuid4().hex
    thread_id = f"{payload.user_id}:{payload.conversation_id}"
    pending = register_pending(correlation_id)

    reply_parts: list[str] = []
    emotion = _neutral_emotion()
    emotion_degraded = False
    reply_degraded = False

    try:
        history = await _load_history(graph, thread_id)

        await bridge.send(
            settings.topic_emotion_request,
            {
                "correlation_id": correlation_id,
                "user_input": payload.message,
                "history": history,
            },
        )

        try:
            raw = await asyncio.wait_for(
                pending.emotion.get(), timeout=settings.emotion_timeout
            )
            if raw.get("error"):
                raise RuntimeError(str(raw["error"]))
            emotion = _normalize_emotion(raw)
        except asyncio.TimeoutError:
            logger.warning("情感识别超时 correlation_id=%s", correlation_id)
            emotion_degraded = True
        except Exception as exc:
            logger.warning(
                "情感识别失败，降级 neutral correlation_id=%s err=%s", correlation_id, exc
            )
            emotion_degraded = True

        yield sse("emotion", emotion)

        await bridge.send(
            settings.topic_reply_request,
            {
                "correlation_id": correlation_id,
                "user_input": payload.message,
                "emotion_result": {**emotion, "correlation_id": correlation_id},
                "history": history,
            },
        )

        loop = asyncio.get_running_loop()
        deadline = loop.time() + settings.reply_timeout
        try:
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise asyncio.TimeoutError
                message = await asyncio.wait_for(pending.reply.get(), timeout=remaining)
                if message.get("error"):
                    logger.warning(
                        "回复生成失败 correlation_id=%s err=%s",
                        correlation_id,
                        message["error"],
                    )
                    reply_degraded = True
                    break
                token = message.get("token") or ""
                if token:
                    reply_parts.append(token)
                    yield sse("token", {"token": token})
                if message.get("is_final"):
                    break
        except asyncio.TimeoutError:
            logger.warning("等待回复超时 correlation_id=%s", correlation_id)
            reply_degraded = True

        if not reply_parts:
            reply_degraded = True
            reply_parts.append(FALLBACK_REPLY)
            yield sse("token", {"token": FALLBACK_REPLY})

        reply = "".join(reply_parts)
        await _record_turn(graph, thread_id, payload.message, emotion, reply)

        yield sse(
            "done",
            {
                "reply": reply,
                "debug": {
                    "correlation_id": correlation_id,
                    "thread_id": thread_id,
                    "emotion_degraded": emotion_degraded,
                    "reply_degraded": reply_degraded,
                },
            },
        )
    except asyncio.CancelledError:
        logger.info("客户端已断开 correlation_id=%s", correlation_id)
        raise
    except Exception:
        logger.exception("对话处理异常 correlation_id=%s", correlation_id)
        yield sse("error", {"message": "服务异常，请稍后重试"})
    finally:
        unregister_pending(correlation_id)


@app.post("/chat")
async def chat(payload: ChatRequest, request: Request) -> StreamingResponse:
    return StreamingResponse(
        _chat_stream(request, payload),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
