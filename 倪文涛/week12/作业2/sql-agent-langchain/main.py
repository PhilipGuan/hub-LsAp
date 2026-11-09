"""FastAPI 应用入口：python main.py。"""
from fastapi import FastAPI

from api.chat import router

app = FastAPI(title="Chinook SQL Agent", version="1.0.0")
app.include_router(router, prefix="/api")

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
