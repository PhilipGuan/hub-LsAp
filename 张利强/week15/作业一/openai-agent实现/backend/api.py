"""FastAPI 后端：接收对话请求，转发 SSE token 流。

- POST /chat/trigger       生成 request_id，把 chat.request 投递到 Kafka
- GET  /chat/stream/{id}   订阅 Redis Pub/Sub，向前端推送 SSE
- GET  /                   前端示例页面

必须单 worker 运行：stream_queues 是进程内内存结构。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from aiokafka import AIOKafkaProducer
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from backend.config import (
    BASE_DIR,
    DASHSCOPE_API_KEY,
    KAFKA_BOOTSTRAP,
    LLM_TIMEOUT_SECONDS,
    LOG_LEVEL,
    QWEN_MODEL,
    REDIS_URL,
)
from backend.contracts import TOPIC_CHAT_REQUEST, ChatRequest

QUEUE_TTL_SECONDS = 300
STREAM_QUEUE_MAXSIZE = 2000
FRONTEND_INDEX = BASE_DIR / "frontend" / "index.html"

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("api")

redis_client: aioredis.Redis | None = None
producer: AIOKafkaProducer | None = None

# request_id -> 待推送事件队列（进程内，必须单 worker）
stream_queues: dict[str, asyncio.Queue] = {}
queue_created: dict[str, float] = {}


class TriggerRequest(BaseModel):
    session_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    message: str = Field(min_length=1)
    clarification_round: int = Field(default=0, ge=0)
    previous_clarification: str | None = None


async def _pubsub_loop() -> None:
    """全局订阅 stream:*，按 request_id 路由到对应队列。"""
    assert redis_client is not None
    while True:
        pubsub = redis_client.pubsub()
        try:
            await pubsub.psubscribe("stream:*")
            async for message in pubsub.listen():
                if message.get("type") != "pmessage":
                    continue
                channel = str(message.get("channel", ""))
                if ":" not in channel:
                    continue
                request_id = channel.split(":", 1)[1]
                queue = stream_queues.get(request_id)
                if queue is None:
                    continue
                try:
                    payload = json.loads(message["data"])
                except (TypeError, ValueError):
                    log.warning("invalid stream payload request_id=%s", request_id)
                    continue
                try:
                    queue.put_nowait(payload)
                except asyncio.QueueFull:
                    log.warning("stream queue full request_id=%s", request_id)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - Redis 抖动时重建订阅
            log.exception("pubsub loop failed, reconnecting")
            await asyncio.sleep(1)
        finally:
            try:
                await pubsub.aclose()
            except Exception:  # noqa: BLE001
                pass


async def _janitor_loop() -> None:
    """清理始终未建立 SSE 连接的孤儿队列。"""
    while True:
        await asyncio.sleep(60)
        now = time.monotonic()
        for request_id, created_at in list(queue_created.items()):
            if now - created_at > QUEUE_TTL_SECONDS:
                stream_queues.pop(request_id, None)
                queue_created.pop(request_id, None)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis_client, producer
    if not DASHSCOPE_API_KEY:
        log.warning(
            "DASHSCOPE_API_KEY 未配置：请在项目根目录 .env 中填写 Qwen API Key，否则对话会失败"
        )
    redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
    producer = AIOKafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP,
        acks="all",
        enable_idempotence=True,
    )
    await producer.start()
    tasks = [
        asyncio.create_task(_pubsub_loop(), name="pubsub"),
        asyncio.create_task(_janitor_loop(), name="janitor"),
    ]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await producer.stop()
        await redis_client.aclose()


app = FastAPI(title="情感支持对话系统", lifespan=lifespan)


@app.post("/chat/trigger")
async def trigger(req: TriggerRequest) -> dict[str, str]:
    if producer is None:
        raise HTTPException(status_code=503, detail="producer not ready")

    request_id = uuid.uuid4().hex
    chat_request = ChatRequest(
        request_id=request_id,
        session_id=req.session_id,
        user_id=req.user_id,
        message=req.message,
        clarification_round=req.clarification_round,
        previous_clarification=req.previous_clarification,
    )

    # 先注册队列再投递，避免首 token 早于 SSE 连接而丢失。
    stream_queues[request_id] = asyncio.Queue(maxsize=STREAM_QUEUE_MAXSIZE)
    queue_created[request_id] = time.monotonic()

    try:
        await producer.send_and_wait(
            TOPIC_CHAT_REQUEST,
            chat_request.to_kafka(),
            key=req.session_id.encode("utf-8"),
        )
    except Exception:  # noqa: BLE001
        stream_queues.pop(request_id, None)
        queue_created.pop(request_id, None)
        log.exception("failed to produce chat.request")
        raise HTTPException(status_code=503, detail="failed to enqueue request") from None

    return {"request_id": request_id}


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _normalize(payload: dict) -> dict:
    """统一对外事件格式：内部 token 事件转换为文档约定的 {"t": ...}。"""
    if payload.get("type") == "token" and "token" in payload:
        return {"t": payload["token"]}
    return payload


async def _mark_cancelled(request_id: str) -> None:
    if redis_client is None:
        return
    try:
        await redis_client.set(f"cancel:{request_id}", "1", ex=60)
    except Exception:  # noqa: BLE001
        log.exception("failed to mark cancelled request_id=%s", request_id)


async def _event_stream(request: Request, request_id: str):
    queue = stream_queues.get(request_id)
    if queue is None:
        return
    try:
        # 立即发一条注释，让代理 / 浏览器确认连接已建立。
        yield ": connected\n\n"
        while True:
            try:
                payload = await asyncio.wait_for(
                    queue.get(),
                    timeout=LLM_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                yield _sse({"error": "timeout"})
                return

            if await request.is_disconnected():
                await _mark_cancelled(request_id)
                return

            payload = _normalize(payload)
            yield _sse(payload)

            if (
                payload.get("done")
                or payload.get("type") == "clarification"
                or "error" in payload
            ):
                return
    except asyncio.CancelledError:
        await _mark_cancelled(request_id)
        raise
    finally:
        stream_queues.pop(request_id, None)
        queue_created.pop(request_id, None)


@app.get("/chat/stream/{request_id}")
async def stream_chat(request_id: str, request: Request) -> StreamingResponse:
    if request_id not in stream_queues:
        raise HTTPException(status_code=404, detail="unknown request_id")
    return StreamingResponse(
        _event_stream(request, request_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # 防止 Nginx 缓冲整个响应体，导致 SSE 退化为一次性返回。
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(FRONTEND_INDEX)
