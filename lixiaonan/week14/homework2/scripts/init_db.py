"""初始化数据库表结构（document / chunk），对应《需求文档》§4。

用法（任意目录）：python scripts/init_db.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.db.base import init_db  # noqa: E402


def main() -> None:
    init_db()
    print("数据库表初始化完成（document / chunk）")


if __name__ == "__main__":
    main()
