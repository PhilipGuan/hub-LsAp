"""Web 服务启动入口：python -m sql_agent.webserver 或 sql-agent-web。"""

from __future__ import annotations

import argparse
import os


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="启动 Chinook SQL Agent 的 FastAPI 服务")
    parser.add_argument(
        "--host",
        default=os.getenv("SQL_AGENT_WEB_HOST", "127.0.0.1"),
        help="监听地址，默认 127.0.0.1（仅本机访问）；对外暴露请显式传入并自行加鉴权",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("SQL_AGENT_WEB_PORT", "8000")),
        help="监听端口，默认 8000",
    )
    parser.add_argument("--reload", action="store_true", help="开发模式：代码变更自动重启")
    args = parser.parse_args()

    uvicorn.run(
        "sql_agent.api:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
