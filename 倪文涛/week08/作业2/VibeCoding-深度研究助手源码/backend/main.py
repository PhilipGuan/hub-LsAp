"""程序入口:python backend/main.py 即可启动服务(浏览器打开 http://127.0.0.1:8000)。"""
import sys
from pathlib import Path

import uvicorn

BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_DIR))   # 保证 --reload 子进程按 "app:app" 导入时能找到模块


def main() -> None:
    uvicorn.run("app:app", host="127.0.0.1", port=7080, reload=True,
                reload_dirs=[str(BACKEND_DIR)])


if __name__ == "__main__":
    main()
