"""在项目根目录运行 python main.py，或使用 README 中的 Uvicorn 命令。"""

import uvicorn


if __name__ == "__main__":
    uvicorn.run("app.api:app", host="0.0.0.0", port=8000)
