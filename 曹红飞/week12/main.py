import base64
import json
import os
import re
from pathlib import Path

from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from openai import OpenAI

app = FastAPI(title="楼道消防隐患识别")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

client = OpenAI(
    api_key="sk-b794a34e9ec34e4b82275385aa8d45ac",
    base_url="https://api.deepseek.com",
)

MAX_IMAGE_BYTES = 10 * 1024 * 1024
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}

VALID_LEVELS = {"none", "low", "medium", "high", "not_corridor", "unknown"}
LEVEL_LABELS = {
    "none": "无隐患",
    "low": "低风险",
    "medium": "中等风险",
    "high": "高风险",
    "not_corridor": "非楼道",
    "unknown": "无法判定",
}

SYSTEM_PROMPT = """你是一名社区消防安全检查员。请仔细观察用户上传的图片，判断它是否拍摄于住宅楼的楼道 / 楼梯间 / 疏散通道，并评估其中的消防安全隐患等级。

【判定等级，必须严格按顺序匹配，命中即停】
1. high（高风险）：画面中出现电动自行车、电动车电池 / 电瓶、飞线充电（从窗户、门缝拉出的电线或插座）、正在充电的电池等可能直接起火的元素。
2. medium（中等风险）：画面中没有上述起火元素，但楼道内存在大量堆积物，已严重阻碍通行；或堆放大量纸箱、木质家具、泡沫、旧衣物、塑料制品等可燃物，一旦起火会迅速蔓延。
3. low（低风险）：楼道内有少量堆物（如一两件杂物、小件物品），但不影响正常通行，可燃物不多，暂不构成明显阻塞。
4. none（无隐患）：楼道干净整洁，地面与通道无堆放物品，通行顺畅。
5. not_corridor（非楼道）：图片与楼道 / 楼梯间 / 疏散通道无关，例如室内房间、厨房、户外街景、风景、人像、宠物、文档截图等。

【判定原则】
- 同时存在多种情况时，取最高的风险等级。
- 存疑时（光线不足、画面模糊、只拍到局部），倾向于给出更高一级的风险，并在 summary 中说明不确定原因。
- 只依据画面中真实可见的元素判断，不要臆测画面外的内容。

【输出要求】
只输出一个 JSON 对象，不要输出任何多余文字、解释或 Markdown 代码块，也不要输出注释。字段格式如下：
{
  "is_corridor": true 或 false,
  "risk_level": "none" | "low" | "medium" | "high" | "not_corridor",
  "confidence": 0 到 1 之间的小数,
  "summary": "一句话说明结论和主要依据，中文，60 字以内",
  "hazards": ["具体隐患点，按严重程度排序，最多 5 条；没有则输出空数组"],
  "suggestion": "针对该场景的整改建议，中文，80 字以内"
}"""


class AnalysisResponse(BaseModel):
    risk_level: str
    risk_label: str
    is_corridor: bool
    confidence: float
    summary: str
    hazards: list[str]
    suggestion: str


def _extract_json(text: str) -> dict:
    """模型偶尔会带 ```json 包裹或前后多余文字，做一次清洗。"""
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    if not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start:end + 1]
    return json.loads(text)


def _call_model(data_url: str) -> str:
    """调用视觉模型；response_format 不被支持时自动降级重试一次。"""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "请按要求分析这张图片。"},
                {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}},
            ],
        },
    ]
    base_kwargs = {
        "model": "deepseek-flash",
        "messages": messages,
        "temperature": 0.2,
    }
    try:
        resp = client.chat.completions.create(
            **base_kwargs, response_format={"type": "json_object"}
        )
    except Exception as e:
        if "response_format" not in str(e):
            raise
        resp = client.chat.completions.create(**base_kwargs)
    return resp.choices[0].message.content or ""


def _normalize(data: dict) -> AnalysisResponse:
    level = str(data.get("risk_level", "")).strip().lower()
    if level not in VALID_LEVELS - {"unknown"}:
        level = "not_corridor" if data.get("is_corridor") is False else "unknown"

    try:
        confidence = float(data.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    hazards_raw = data.get("hazards") or []
    if not isinstance(hazards_raw, list):
        hazards_raw = [str(hazards_raw)]
    hazards, seen = [], set()
    for h in hazards_raw:
        h = str(h).strip()
        if h and h not in seen:
            seen.add(h)
            hazards.append(h)
    hazards = hazards[:5]

    return AnalysisResponse(
        risk_level=level,
        risk_label=LEVEL_LABELS[level],
        is_corridor=(level != "not_corridor") if "is_corridor" not in data else bool(data["is_corridor"]),
        confidence=round(confidence, 2),
        summary=str(data.get("summary") or "模型未给出结论摘要").strip(),
        hazards=hazards,
        suggestion=str(data.get("suggestion") or "建议现场人工复核。").strip(),
    )


@app.get("/")
async def index():
    index_file = FRONTEND_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=500, detail=f"找不到前端文件: {index_file}")
    return FileResponse(index_file)


@app.get("/favicon.ico")
async def favicon():
    icon = FRONTEND_DIR / "favicon.ico"
    if icon.exists():
        return FileResponse(icon)
    return Response(status_code=204)


@app.post("/analyze", response_model=AnalysisResponse)
async def analyze_image(file: UploadFile = File(...)):
    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="上传的图片为空")
    if len(contents) > MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"图片过大，请上传小于 {MAX_IMAGE_BYTES // (1024 * 1024)} MB 的图片",
        )
    if file.content_type and file.content_type not in ALLOWED_TYPES:
        raise HTTPException(status_code=415, detail=f"不支持的图片格式：{file.content_type}")

    mime = file.content_type or "image/jpeg"
    data_url = f"data:{mime};base64,{base64.b64encode(contents).decode('utf-8')}"

    try:
        raw = _call_model(data_url)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"DeepSeek API 调用失败: {e}")

    try:
        parsed = _extract_json(raw)
    except Exception:
        raise HTTPException(
            status_code=502,
            detail=f"模型返回内容无法解析为 JSON，原始输出：{raw[:200]}",
        )

    return _normalize(parsed)


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
