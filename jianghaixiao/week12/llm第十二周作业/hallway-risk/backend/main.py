"""FastAPI 入口（骨架）。

启动：
    cd backend
    python -m uvicorn main:app --reload --port 8000
"""
from fastapi import FastAPI, File, HTTPException, UploadFile

from config import MAX_IMAGE_MB
from schemas import BasicResponse, RiskAssessment
from vision import analyze_image

app = FastAPI(title="楼道消防隐患识别 API", version="0.1")


@app.get("/v1/health")
def health():
    return {"status": "ok"}


@app.post("/v1/risk/analyze", response_model=BasicResponse)
async def analyze(file: UploadFile = File(...)):
    """上传一张图片，返回风险评估结果。

    TODO:
    1. 校验 file.content_type in {"image/jpeg", "image/png", "image/webp"}
    2. data = await file.read()
    3. 校验 len(data) <= MAX_IMAGE_MB * 1024 * 1024
    4. result = analyze_image(data)
    5. return BasicResponse(code=200, message="ok", data=result)
    6. 异常：返回 code=400/500 + message
    """
    raise HTTPException(status_code=501, detail="TODO: 实现图片分析与接口逻辑")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)