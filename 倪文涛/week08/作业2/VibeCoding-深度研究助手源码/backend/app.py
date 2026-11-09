"""FastAPI 应用与路由:首页、SSE 研究事件流接口。"""
import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from agents import AgentError
from config import CONFIG
from orchestrator import run_research

BACKEND_DIR = Path(__file__).resolve().parent
FRONTEND_DIST = BACKEND_DIR.parent / "frontend" / "dist"   # npm run build 产物

app = FastAPI(title="深度研究助手")
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")


@app.get("/")
async def index():
    """已构建(npm run build)则伺服前端产物;开发模式请走 Vite(127.0.0.1:5173)。"""
    index_html = FRONTEND_DIST / "index.html"
    if index_html.exists():
        return FileResponse(index_html)
    return PlainTextResponse(
        "前端未构建。开发模式:cd frontend && npm run dev(访问 http://127.0.0.1:6174);"
        "或先 npm run build 再重启后端,即可从本端口(7080)访问。"
    )


@app.get("/api/research")
async def research(topic: str = Query(..., min_length=2)):
    """SSE:研究过程事件流,以 report → done 结束。"""
    topic = topic.strip()

    async def gen():
        queue: asyncio.Queue = asyncio.Queue()

        async def emit(ev: dict) -> None:
            await queue.put(ev)

        async def worker() -> None:
            try:
                result = await asyncio.wait_for(
                    run_research(topic, emit), timeout=CONFIG.overall_timeout_seconds)
                await queue.put({"type": "report", **result})
            except asyncio.TimeoutError:
                await queue.put({"type": "error", "message":
                    f"研究超时({CONFIG.overall_timeout_seconds} 秒)已中止。"
                    "可在 .env 调大 OVERALL_TIMEOUT_SECONDS 或调小 MAX_ROUNDS 后重试。"})
            except AgentError as e:
                await queue.put({"type": "error", "message": str(e)})
            except Exception as e:  # 兜底:任何未预期错误都以事件形式通知,不挂死连接
                await queue.put({"type": "error", "message": f"未预期的错误:{e}"})
            finally:
                await queue.put({"type": "done"})

        asyncio.create_task(worker())
        while True:
            ev = await queue.get()
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            if ev.get("type") == "done":
                break

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
