# -*- coding: utf-8 -*-
"""
SQL Agent (LangGraph) Web 服务

复用 sql_agent_langgraph.py 里的 create_sql_agent / create_demo_db，
对外提供两个接口：

    GET  /                 -> 前端页面 static/index.html
    POST /api/chat         -> 非流式：一次返回最终回答 + 工具调用轨迹
    POST /api/chat/stream  -> 流式(SSE)：逐 token 返回回答，并实时推送工具调用事件

启动：
    pip install -r requirements.txt
    uvicorn app:app --reload --port 8000
    浏览器打开 http://127.0.0.1:8000
"""

import json
import os
import sys
from pathlib import Path

# Windows 控制台默认 GBK，统一 UTF-8，避免打印中文崩溃
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from langchain_openai import ChatOpenAI

from sql_agent_langgraph import (
    DB_PATH,
    create_demo_db,
    create_sql_agent,
    get_final_answer,
)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DB_URL = f"sqlite:///{DB_PATH}"

app = FastAPI(title="ChatBI — SQL Agent (LangGraph)")

# 前端单独托管，正常同源访问无需 CORS；加上以便本地调试时跨端口调用
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# 初始化：确保演示库存在 + 构建一次 agent（进程内复用）
# ============================================================
def _build_model() -> ChatOpenAI:
    return ChatOpenAI(
        model=os.getenv("SQL_AGENT_MODEL", "deepseek-chat"),
        base_url=os.getenv("SQL_AGENT_BASE_URL", "https://api.deepseek.com"),
        api_key=os.getenv("DEEPSEEK_API_KEY", "sk-"),
        temperature=0.1,
    )


if not os.path.exists(DB_PATH):
    create_demo_db(DB_URL)

_agent = create_sql_agent(_build_model(), DB_URL)


# ============================================================
# 请求模型
# ============================================================
class ChatRequest(BaseModel):
    messages: list[dict]  # [{"role": "user"|"assistant", "content": "..."}]


def _to_lc_messages(messages: list[dict]) -> list:
    """把前端传来的 [{role, content}] 转成 LangGraph 可接受的 (role, content) 列表。"""
    lc = []
    for m in messages or []:
        role = m.get("role")
        content = (m.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            lc.append((role, content))
    return lc


def _extract_trace(result: dict) -> list:
    """把消息列表里的工具调用轨迹抽成 [{name, args}]。"""
    trace = []
    for m in result.get("messages", []):
        if getattr(m, "type", "") == "ai":
            for tc in getattr(m, "tool_calls", []) or []:
                trace.append({"name": tc.get("name"), "args": tc.get("args")})
    return trace


# ============================================================
# 路由
# ============================================================
@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/chat")
def chat(req: ChatRequest):
    """非流式：一次性返回最终回答和工具调用轨迹（支持多轮历史）。"""
    lc_messages = _to_lc_messages(req.messages)
    if not lc_messages:
        return JSONResponse(status_code=400, content={"error": "问题不能为空"})
    try:
        result = _agent.invoke({"messages": lc_messages})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"调用模型失败：{e}"})
    return {
        "answer": get_final_answer(result),
        "trace": _extract_trace(result),
    }


@app.post("/api/chat/stream")
async def chat_stream(req: ChatRequest):
    """流式：SSE 逐 token 返回，并推送工具调用事件（支持多轮历史）。"""
    lc_messages = _to_lc_messages(req.messages)
    if not lc_messages:
        return JSONResponse(status_code=400, content={"error": "问题不能为空"})

    async def event_stream():
        try:
            async for event in _agent.astream_events(
                {"messages": lc_messages},
                version="v2",
            ):
                kind = event["event"]
                if kind == "on_chat_model_stream":
                    content = event["data"]["chunk"].content
                    if content:
                        yield _sse({"type": "token", "content": content})
                elif kind == "on_tool_start":
                    yield _sse({
                        "type": "tool_start",
                        "name": event.get("name", ""),
                        "args": event["data"].get("input", {}),
                    })
                elif kind == "on_tool_end":
                    output = event["data"].get("output")
                    yield _sse({
                        "type": "tool_end",
                        "name": event.get("name", ""),
                        "output": str(output)[:300],
                    })
        except Exception as e:  # 模型/网络报错，推给前端展示
            yield _sse({"type": "error", "content": f"调用模型失败：{e}"})
        finally:
            yield _sse({"type": "done"})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(payload: dict) -> str:
    """把一个 dict 编码成一条 SSE data 帧。"""
    return f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
