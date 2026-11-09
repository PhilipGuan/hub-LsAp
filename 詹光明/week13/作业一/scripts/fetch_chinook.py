"""下载 Chinook SQLite 示例数据库到项目 data/ 目录。

用法：
    python scripts/fetch_chinook.py            # 下载到 data/chinook.db
    python scripts/fetch_chinook.py --force    # 覆盖已有文件
"""

from __future__ import annotations

import argparse
import sys
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "chinook.db"
DEFAULT_URLS = [
    "https://github.com/lerocha/chinook-database/releases/download/v1.4.5/Chinook_Sqlite.sqlite",
]
SQLITE_HEADER = b"SQLite format 3\x00"


def _download(url: str, destination: Path, timeout: float = 60.0) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")

    request = urllib.request.Request(
        url, headers={"User-Agent": "sql-agent-homework/1.0"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        with temporary.open("wb") as handle:
            while True:
                chunk = response.read(1024 * 256)
                if not chunk:
                    break
                handle.write(chunk)

    with temporary.open("rb") as handle:
        header = handle.read(len(SQLITE_HEADER))
    if header != SQLITE_HEADER:
        temporary.unlink(missing_ok=True)
        raise ValueError(f"下载内容不是 SQLite 数据库：{url}")

    temporary.replace(destination)


def main() -> None:
    parser = argparse.ArgumentParser(description="下载 Chinook SQLite 数据库")
    parser.add_argument("--url", help="自定义下载地址（默认使用官方 GitHub Release）")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"保存路径，默认 {DEFAULT_OUTPUT}",
    )
    parser.add_argument("--force", action="store_true", help="覆盖已有文件")
    args = parser.parse_args()

    output: Path = args.output
    if not output.is_absolute():
        output = PROJECT_ROOT / output

    if output.exists() and not args.force:
        print(f"数据库已存在：{output}（使用 --force 覆盖）")
        return

    urls = [args.url] if args.url else DEFAULT_URLS
    last_error: Exception | None = None
    for url in urls:
        try:
            print(f"正在下载 {url}")
            _download(url, output)
            print(f"完成：{output}（{output.stat().st_size / 1024 / 1024:.1f} MB）")
            return
        except Exception as exc:  # noqa: BLE001 - 依次尝试备用地址
            last_error = exc
            print(f"下载失败：{exc}", file=sys.stderr)

    raise SystemExit(f"无法下载 Chinook 数据库：{last_error}")


if __name__ == "__main__":
    main()
