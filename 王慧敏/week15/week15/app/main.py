"""后端进程：FastAPI。

- 接收前端（用户）输入；
- 串联进程1(情感识别) + 进程2(鼓励/排解)；
- 同时托管前端静态页面。
"""
from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config
from . import langgraph_impl, openai_agents_impl

app = FastAPI(title="情感识别与回应服务", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

IMPLS = {
    "langgraph": langgraph_impl.run,
    "openai-agents": openai_agents_impl.run,
}


class AnalyzeRequest(BaseModel):
    text: str
    impl: str = ""  # langgraph | openai-agents，为空用默认


@app.get("/api/health")
async def health():
    return {"status": "ok", "has_llm": config.has_llm(), "impls": list(IMPLS)}


@app.post("/api/analyze")
async def analyze(req: AnalyzeRequest):
    text = (req.text or "").strip()
    if not text:
        return {"error": "输入不能为空"}

    impl = (req.impl or config.DEFAULT_IMPL).strip()
    runner = IMPLS.get(impl)
    if runner is None:
        return {"error": f"未知实现 {impl!r}，可选 {list(IMPLS)}"}

    result = await runner(text)
    result["impl"] = impl
    return result


# 托管前端静态页面（放在 API 路由之后，避免抢占 /api/*）
_FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
if os.path.isdir(_FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=_FRONTEND_DIR, html=True), name="frontend")
