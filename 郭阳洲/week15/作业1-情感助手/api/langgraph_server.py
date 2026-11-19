"""作业1 · 接口层：LangGraph 后端入口（端口 8001）。

启动（在「作业1-情感助手」目录下）：
    python -m api.langgraph_server
"""
from __future__ import annotations

from api.server_factory import create_app
from flows.langgraph_flow import run_pipeline, run_pipeline_stream

app = create_app("langgraph", run_pipeline, run_pipeline_stream)


if __name__ == "__main__":
    import uvicorn

    from core.config import LANGGRAPH_PORT

    uvicorn.run(app, host="127.0.0.1", port=LANGGRAPH_PORT)