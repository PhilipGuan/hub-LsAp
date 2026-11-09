import asyncio
from contextlib import suppress
from threading import Event
from time import monotonic

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from models.chat import ChatEvent, ChatRequest, ChatResponse, ErrorEvent
from models.workflow import WorkflowError
from services.chat_service import stream_ask

router = APIRouter()


@router.get("/health")
def health():
    return {"status": "ok"}


async def event_stream(request: Request, body: ChatRequest):
    cancel = Event()
    source = stream_ask(body, cancel=cancel)
    pending = None
    last_sent = monotonic()
    try:
        pending = asyncio.create_task(anext(source))
        while True:
            ready, _ = await asyncio.wait({pending}, timeout=0.5)
            if await request.is_disconnected():
                break
            if ready:
                try:
                    event = pending.result()
                except StopAsyncIteration:
                    raise RuntimeError("Workflow ended before done")
                yield event.encode()
                last_sent = monotonic()
                if event.event == "done":
                    break
                pending = asyncio.create_task(anext(source))
            elif monotonic() - last_sent >= 15:
                yield ": ping\n\n"
                last_sent = monotonic()
    except asyncio.CancelledError:
        raise
    except Exception:
        error = WorkflowError(stage="stream", code="internal_error", message="事件流处理失败，请重新提交。")
        yield ChatEvent(event="error", data=ErrorEvent(stage=error.stage, code=error.code,
                        message=error.message, fatal=True)).encode()
        yield ChatEvent(event="done", data=ChatResponse(error=error, answer=error.message)).encode()
    finally:
        cancel.set()
        if pending is not None:
            pending.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await pending
        with suppress(asyncio.CancelledError, Exception):
            await source.aclose()


@router.post("/chat", response_class=StreamingResponse)
async def chat(body: ChatRequest, request: Request):
    return StreamingResponse(event_stream(request, body), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
