#!/usr/bin/env python3
"""项目统一启动入口，免去手动设置 PYTHONPATH。

用法（在仓库根目录执行）：

    python run.py                          # 启动 Web 服务，默认 http://127.0.0.1:8000/
    python run.py --reload --port 8080     # 开发模式：改端口并热重载
    python run.py "总共有多少张专辑？"      # 命令行提问，结果以 JSON 输出
    python run.py --cli                    # 强制命令行模式，问题从标准输入读取

Web 模式下 API Key 可先在页面右上角“接口设置”中临时填写；
命令行模式必须在环境变量或根目录 .env 中配置 OPENAI_API_KEY。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


def _bootstrap() -> None:
    """把 src 加入导入路径，并同步到 PYTHONPATH，供 uvicorn --reload 的子进程使用。"""

    src = str(SRC_DIR)
    if src not in sys.path:
        sys.path.insert(0, src)

    parts = [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]
    if src not in parts:
        os.environ["PYTHONPATH"] = os.pathsep.join([src, *parts])


def _load_dotenv(path: Path) -> None:
    """加载根目录 .env（若存在）；已设置的环境变量优先，不覆盖。"""

    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        line = line.removeprefix("export ")
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Chinook SQL Agent 启动入口：默认启动 Web 界面，带问题参数时执行命令行查询。",
    )
    parser.add_argument("question", nargs="*", help="自然语言查询问题；提供时走命令行模式")
    parser.add_argument("--cli", action="store_true", help="强制命令行模式，问题从参数或标准输入读取")
    parser.add_argument(
        "--host",
        default=os.getenv("UVICORN_HOST", "127.0.0.1"),
        help="Web 监听地址，默认读取 UVICORN_HOST 或 127.0.0.1",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=_env_int("UVICORN_PORT", 8000),
        help="Web 监听端口，默认读取 UVICORN_PORT 或 8000",
    )
    parser.add_argument("--reload", action="store_true", help="开发模式：代码变更后自动重载")
    parser.add_argument(
        "--log-level",
        type=str.upper,
        default=os.getenv("SQL_AGENT_LOG_LEVEL", "INFO"),
        choices=LOG_LEVELS,
        help="日志级别，默认读取 SQL_AGENT_LOG_LEVEL 或 INFO",
    )
    return parser.parse_args()


def _run_cli(question: list[str], log_level: str) -> None:
    """复用 sql_agent.cli：结果 JSON 写到标准输出，日志写到标准错误。"""

    from sql_agent.cli import main

    sys.argv = ["sql-agent", *question, "--log-level", log_level]
    main()


def _run_web(args: argparse.Namespace) -> None:
    import uvicorn

    from sql_agent.config import settings

    os.environ["SQL_AGENT_LOG_LEVEL"] = args.log_level

    display_host = "127.0.0.1" if args.host in {"0.0.0.0", "::"} else args.host
    print(
        "Chinook SQL Agent 正在启动\n"
        f"  数据库    : {settings.db_path}\n"
        f"  模型      : {settings.primary_model} / 摘要 {settings.summary_model}\n"
        f"  接口地址  : {settings.base_url}\n"
        f"  访问入口  : http://{display_host}:{args.port}/\n"
        f"  接口文档  : http://{display_host}:{args.port}/api/docs",
        file=sys.stderr,
        flush=True,
    )
    if not os.getenv("OPENAI_API_KEY"):
        print(
            "  提示      : 未检测到 OPENAI_API_KEY，可在页面右上角“接口设置”中填写。",
            file=sys.stderr,
            flush=True,
        )

    try:
        uvicorn.run(
            "sql_agent.api:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
            reload_dirs=[str(SRC_DIR)] if args.reload else None,
            log_level=args.log_level.lower(),
        )
    except OSError as exc:
        print(f"启动失败：{exc}\n端口可能已被占用，可通过 --port 换一个端口。", file=sys.stderr)
        raise SystemExit(1) from exc
    except KeyboardInterrupt:
        print("\n已停止。", file=sys.stderr)


def main() -> None:
    _bootstrap()
    _load_dotenv(PROJECT_ROOT / ".env")
    args = _parse_args()

    if args.cli or args.question:
        _run_cli(args.question, args.log_level)
    else:
        _run_web(args)


if __name__ == "__main__":
    main()
