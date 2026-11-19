"""LangGraph 版本：组装并运行四个进程。

    python run_langgraph.py                       # CLI 前端
    python run_langgraph.py --web                  # FastAPI + WebSocket 前端
    python run_langgraph.py --text "今天升职了！"   # 非交互
"""
from __future__ import annotations

import argparse
import multiprocessing as mp

from langgraph_app.backend import build_orchestrator
from langgraph_app.processes import handle_sentiment, handle_support
from shared.runtime import backend_main, frontend_main, serve


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="LangGraph 版：情感识别 → 鼓励/排解（四进程流水线）"
    )
    parser.add_argument(
        "--text",
        action="append",
        metavar="TEXT",
        help="非交互模式：处理一条输入后退出，可重复指定",
    )
    parser.add_argument(
        "--web",
        action="store_true",
        help="用 FastAPI + WebSocket 替代 CLI 前端进程",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Web 模式监听地址")
    parser.add_argument("--port", type=int, default=8000, help="Web 模式监听端口")
    args = parser.parse_args(argv)

    if args.web and args.text:
        parser.error("--web 与 --text 不能同时使用")
    return args


def build_processes(ctx: mp.context.BaseContext, args: argparse.Namespace) -> list[mp.Process]:
    front_in = ctx.Queue()
    front_out = ctx.Queue()
    sentiment_in = ctx.Queue()
    sentiment_out = ctx.Queue()
    support_in = ctx.Queue()
    support_out = ctx.Queue()

    processes = [
        ctx.Process(
            target=serve,
            args=(handle_sentiment, sentiment_in, sentiment_out),
            name="process-1-sentiment",
        ),
        ctx.Process(
            target=serve,
            args=(handle_support, support_in, support_out),
            name="process-2-support",
        ),
        ctx.Process(
            target=backend_main,
            args=(
                front_in,
                front_out,
                sentiment_in,
                sentiment_out,
                support_in,
                support_out,
                build_orchestrator,
            ),
            name="backend",
        ),
    ]

    if args.web:
        # 只有 Web 模式才导入 fastapi / uvicorn，CLI 模式不依赖它们
        from shared.web_frontend import web_frontend_main

        frontend = ctx.Process(
            target=web_frontend_main,
            args=(front_in, front_out, args.host, args.port),
            name="frontend-api",
        )
    else:
        frontend = ctx.Process(
            target=frontend_main,
            args=(front_in, front_out, args.text, "LangGraph"),
            name="frontend",
        )

    processes.append(frontend)
    return processes


def main(argv: list[str] | None = None) -> None:
    mp.freeze_support()
    args = parse_args(argv)

    ctx = mp.get_context("spawn")
    processes = build_processes(ctx, args)

    for process in processes:
        process.start()

    try:
        for process in processes:
            process.join()
    except KeyboardInterrupt:
        for process in processes:
            if process.is_alive():
                process.terminate()
        for process in processes:
            process.join(timeout=5)


if __name__ == "__main__":
    main()
