import asyncio
import logging
import time
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.sse import EventSourceResponse
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

from backend.common.kafka_utils import commit_record, publish
from backend.model.schemas import ChatRequest, ErrorResult, UserInput, result_adapter

logger = logging.getLogger(__name__)
router = APIRouter()


class ResultDispatcher:
    def __init__(self):
        # ponytail: process-local state; a shared result store is needed for multiple API workers.
        self.pending = {}

    def register(self, request_id):
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        return future

    def discard(self, request_id):
        future = self.pending.pop(request_id, None)
        if future is not None and not future.done():
            future.cancel()

    def deliver(self, result):
        future = self.pending.get(result.request_id)
        if future is not None and not future.done():
            future.set_result(result)
            return True
        return False

    def fail_pending(self):
        for request_id in list(self.pending):
            self.deliver(ErrorResult(request_id=request_id, stage="backend",
                                     code="RESULT_CONSUMER_STOPPED", message="结果接收服务中断，请稍后重新提交。"))

    async def consume(self, consumer):
        logger.info("Kafka 结果消费任务已启动")
        try:
            async for record in consumer:
                try:
                    result = result_adapter.validate_json(record.value)
                except ValueError:
                    logger.warning("收到无效 Kafka 回复消息，已忽略 topic=%s partition=%s offset=%s",
                                   record.topic, record.partition, record.offset)
                else:
                    logger.info("request_id=%s 收到 Kafka 回复消息 topic=%s partition=%s offset=%s status=%s",
                                result.request_id, record.topic, record.partition, record.offset, result.status)
                    if self.deliver(result):
                        logger.info("request_id=%s 回复已匹配等待请求 status=%s", result.request_id, result.status)
                    else:
                        logger.info("request_id=%s 忽略过期或重复回复", result.request_id)
                await commit_record(consumer, record)
        finally:
            logger.info("Kafka 结果消费任务已停止 pending_requests=%s", len(self.pending))
            self.fail_pending()


class ClosingEventSourceResponse(EventSourceResponse):
    async def __call__(self, scope, receive, send):
        cleanup, self.background = self.background, None
        try:
            await super().__call__(scope, receive, send)
        finally:
            # ASGI 2.4 reports disconnects as send errors; close the generator even then.
            try:
                await self.body_iterator.aclose()
            finally:
                if cleanup is not None:
                    await cleanup()


@router.get("/health")
async def health(request: Request):
    app = request.app
    if app.state.consumer_task.done():
        raise HTTPException(503, "结果消费者不可用，请重启后端")
    return {"status": "ok"}


@router.post("/chat", response_class=StreamingResponse,
             responses={200: {"content": {"text/event-stream": {}}}})
async def chat(body: ChatRequest, request: Request):
    app = request.app
    settings = app.state.settings
    await health(request)
    request_id = uuid4()
    started = time.monotonic()
    outcome = "interrupted"
    logger.info("request_id=%s 收到聊天请求 input_chars=%s", request_id, len(body.message))
    dispatcher = app.state.dispatcher
    future = dispatcher.register(request_id)
    try:
        await publish(app.state.producer, settings.kafka_request_topic,
                      UserInput(request_id=request_id, user_input=body.message))
    except BaseException as exc:
        dispatcher.discard(request_id)
        if not isinstance(exc, Exception):
            raise
        raise HTTPException(503, "消息发送失败，请稍后重新提交") from None

    async def events():
        nonlocal outcome
        logger.info("request_id=%s SSE 开始等待结果 timeout_seconds=%s",
                    request_id, settings.request_timeout_seconds)
        deadline = asyncio.get_running_loop().time() + settings.request_timeout_seconds
        try:
            while not future.done():
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    logger.warning("request_id=%s 等待回复超时 elapsed=%.3fs",
                                   request_id, time.monotonic() - started)
                    dispatcher.deliver(ErrorResult(
                        request_id=request_id, stage="backend", code="REQUEST_TIMEOUT",
                        message="等待回复超时，请重新提交。"))
                    break
                done, _ = await asyncio.wait([future], timeout=min(15, remaining))
                if not done:
                    yield ": ping\n\n"
            result = future.result()
            event = "result" if result.status == "success" else "error"
            yield f"event: {event}\ndata: {result.model_dump_json()}\n\n"
            outcome = result.status
        finally:
            dispatcher.discard(request_id)

    # Native EventSourceResponse marks the media type; explicit wire encoding also
    # allows publication failures to return HTTP 503 before response headers are sent.
    async def cleanup():
        dispatcher.discard(request_id)
        logger.info("request_id=%s SSE 连接关闭 outcome=%s elapsed=%.3fs",
                    request_id, outcome, time.monotonic() - started)

    return ClosingEventSourceResponse(
        events(), headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        background=BackgroundTask(cleanup),
    )

