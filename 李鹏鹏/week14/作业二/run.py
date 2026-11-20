"""一键启动脚本。

    python run.py                 # 启动 Web + 内嵌解析 worker
    python run.py --reload        # 开发模式热重载
    python run.py --no-worker     # 只跑 Web，另开进程 python -m app.worker
    python run.py --port 9000     # 换端口

注意：命令行参数会写进环境变量后再导入 app.config，
因此和 .env / 系统环境变量的优先级是：命令行 > 环境变量 > .env > 默认值。
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import webbrowser
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="多模态 RAG 文档问答（Kafka + MinerU + FastAPI）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--host", help="监听地址，默认取 HOST 或 0.0.0.0")
    parser.add_argument("--port", type=int, help="监听端口，默认取 PORT 或 8000")
    parser.add_argument("--reload", action="store_true", help="开发模式：代码变更自动重载")
    parser.add_argument("--no-worker", action="store_true", help="不启动内置解析线程")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    return parser.parse_args()


def lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def main() -> int:
    args = parse_args()
    if args.host:
        os.environ["HOST"] = args.host
    if args.port:
        os.environ["PORT"] = str(args.port)
    if args.no_worker:
        os.environ["EMBED_WORKER"] = "0"
    if args.no_browser:
        os.environ["OPEN_BROWSER"] = "0"

    sys.path.insert(0, str(BASE_DIR))

    import uvicorn

    from app.config import settings
    from app.logging_setup import setup_logging

    setup_logging()

    if not (BASE_DIR / ".env").is_file():
        print("  [提示] 未找到 .env，将使用默认配置；可从 .env.example 复制一份。\n")

    url = f"http://127.0.0.1:{settings.port}"
    print()
    print("  多模态 RAG 文档问答")
    print("  " + "-" * 56)
    print(f"  本机访问   : {url}")
    if settings.host in {"0.0.0.0", "::"}:  # noqa: S104 - 局域网演示需要监听全部网卡
        print(f"  局域网访问 : http://{lan_ip()}:{settings.port}")
    print(f"  Kafka      : {settings.kafka_bootstrap} ({'on' if settings.kafka_enabled else 'off'})")
    print(f"  解析后端   : {settings.parser_backend}")
    print(f"  检索模式   : {settings.retrieval_mode}")
    if settings.llm_mock:
        print("  大模型     : 离线演示模式 (LLM_MOCK=1)")
    elif settings.llm_ready:
        print(f"  大模型     : {settings.provider_label} · {settings.llm_model}")
    else:
        print("  大模型     : 未配置（在 .env 填 LLM_API_KEY，或设 LLM_MOCK=1）")
    print(f"  解析 worker: {'内嵌线程' if settings.embed_worker else '未内嵌（请单独运行 python -m app.worker）'}")
    print("  " + "-" * 56)
    print()

    if settings.open_browser:
        threading.Timer(1.2, webbrowser.open, args=(url,)).start()

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=args.reload,
        log_level="info",
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
