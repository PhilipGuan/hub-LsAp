"""作业1 · 接口层：OpenAI-Agents 后端入口（端口 8002）。

启动（在「作业1-情感助手」目录下）：
    python -m api.agents_server
"""
from __future__ import annotations

from api.server_factory import create_app
from flows.agents_flow import run_pipeline, run_pipeline_stream

app = create_app("agents", run_pipeline, run_pipeline_stream)


if __name__ == "__main__":
    import uvicorn

    from core.config import AGENTS_PORT

    uvicorn.run(app, host="127.0.0.1", port=AGENTS_PORT)