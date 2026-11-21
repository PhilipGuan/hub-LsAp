"""后端进程1：接收前端的情感描述，转发给进程2，并把最终处理结果返回前端。"""
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

PROCESS2_URL = "http://127.0.0.1:8002/analyze"
FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"

app = FastAPI(title="process1_api")


class AnalyzeRequest(BaseModel):
    text: str


@app.get("/")
async def index():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.post("/analyze")
async def analyze(req: AnalyzeRequest):
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(PROCESS2_URL, json={"text": req.text})
            resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as exc:
        return JSONResponse(status_code=502, content={"detail": f"后端服务不可用：{exc}"})


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8001)
