"""作业2：网页对话式 NL2SQL，后端复用作业1 的 LangGraph 图。"""

import os
import sys
from pathlib import Path

if not os.getenv("DASHSCOPE_API_KEY"):
    raise SystemExit("请先设置 DASHSCOPE_API_KEY")

sys.path.insert(0, str(Path(__file__).parent.parent / "homework1"))

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

import graph_agent

app = FastAPI()
HISTORY = {}  # thread_id -> [(问, 答)]，用来补指代


class Chat(BaseModel):
    question: str
    thread_id: str = "default"


@app.get("/")
def index():
    return FileResponse(Path(__file__).with_name("index.html"))


@app.post("/api/chat")
def chat(body: Chat):
    q = body.question.strip()
    hist = HISTORY.setdefault(body.thread_id, [])
    ctx = "".join(f"用户：{a}\n助手：{b}\n" for a, b in hist[-3:])

    try:
        r = graph_agent.run(f"此前对话：\n{ctx}\n当前问题：{q}" if ctx else q)
    except Exception as e:
        return {"question": q, "answer": f"服务异常：{e}", "sql": "", "tables": [],
                "row_count": 0, "truncated": False, "evidence": [], "caveats": []}

    hist.append((q, r["answer"]))
    r["question"] = q
    return r
