from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from sql_agent.config import get_settings
from sql_agent.graph import (
    agent_input,
    catalog,
    final_payload,
    make_config,
    sql_agent,
)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
settings = get_settings()

app = FastAPI(
    title="LangGraph SQL Agent",
    description="用 LangGraph 复现的表选择、SQL 生成、执行、修复和总结工作流",
    version="1.0.0",
)

# 便于前后端分开部署时联调；同源部署时实际上不会触发 CORS。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    thread_id: str = Field(min_length=1, max_length=100)


def _validate_request(request: ChatRequest) -> None:
    if not request.message.strip():
        raise HTTPException(status_code=400, detail="问题不能为空")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", request.thread_id):
        raise HTTPException(
            status_code=400,
            detail="thread_id 只能包含字母、数字、下划线和连字符",
        )


def _sse(event: str, payload: dict[str, Any]) -> str:
    data = json.dumps(payload, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {data}\n\n"


_STEP_LABELS = {
    "prepare": "准备数据库上下文",
    "select_tables": "分析问题并选择相关数据表",
    "generate_sql": "生成只读 SQL",
    "execute_sql": "执行 SQL",
    "repair_sql": "根据报错修复 SQL",
    "summarize_answer": "整理自然语言答案",
}


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "database_tables": len(catalog.table_names),
        "llm_configured": bool(settings.llm_api_key),
        "model": settings.llm_model,
    }


@app.get("/api/examples")
def examples() -> dict[str, list[str]]:
    return {
        "items": [
            "每位员工负责的客户数量是多少？",
            "哪个国家的客户消费总额最高？",
            "销量最高的 5 位艺术家是谁？",
            "按音乐类型统计歌曲数量，取前 10 名。",
            "哪个客户的累计消费金额最高？",
        ]
    }


@app.post("/api/chat")
def chat(request: ChatRequest) -> dict[str, Any]:
    _validate_request(request)
    config = make_config(request.thread_id)
    try:
        state = sql_agent.invoke(
            agent_input(request.message.strip()),
            config=config,
        )
        return final_payload(state)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"SQL Agent 执行失败：{exc}",
        ) from exc


@app.post("/api/chat/stream")
def chat_stream(request: ChatRequest) -> StreamingResponse:
    _validate_request(request)
    config = make_config(request.thread_id)

    def event_stream() -> Iterator[str]:
        try:
            for chunk in sql_agent.stream(
                agent_input(request.message.strip()),
                config=config,
                stream_mode="updates",
            ):
                for node_name, update in chunk.items():
                    update = update or {}
                    detail: dict[str, Any] = {
                        "node": node_name,
                        "label": _STEP_LABELS.get(node_name, node_name),
                    }

                    if node_name == "select_tables":
                        detail["selected_tables"] = update.get("selected_tables", [])
                    elif node_name == "generate_sql":
                        detail["sql"] = update.get("sql", "")
                    elif node_name == "execute_sql":
                        detail["success"] = not bool(update.get("sql_error"))
                        detail["row_count"] = len(update.get("rows", []))
                        if update.get("sql_error"):
                            detail["error"] = update["sql_error"][:500]
                    elif node_name == "repair_sql":
                        detail["sql"] = update.get("sql", "")

                    yield _sse("step", detail)

            snapshot = sql_agent.get_state(config)
            payload = final_payload(dict(snapshot.values))
            yield _sse("result", payload)
        except Exception as exc:
            yield _sse(
                "error",
                {"message": f"SQL Agent 执行失败：{exc}"},
            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="web")
