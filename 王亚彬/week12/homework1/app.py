import base64
import io
import json
import os
import time

from fastapi import FastAPI, File, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from openai import OpenAI
from PIL import Image, ImageOps


API_KEY = os.getenv("DASHSCOPE_API_KEY")
client = OpenAI(api_key=API_KEY,
                base_url="https://maas.qianwenaiapi.com/compatible-mode/v1")
MODEL = "qwen3.8-flash"

app = FastAPI()

PROMPT = """判断这张图的场景和楼道消防隐患等级。

不是住宅楼道/楼梯间/疏散通道（如室内房间、室外街道、运动场所）→ risk_level 输出「非楼道」，hazards 为空。
是楼道则按下面标准判定，同时符合多档取最高档：
- 高风险：有电动车、电瓶、飞线充电、私拉电线等可能起火的元素。
- 中风险：大量堆积物严重影响通行，或堆放纸箱、木质家具等可燃物。
- 低风险：有堆物但不严重，不影响通行。
- 无风险：楼道干净，无堆放物品。

只描述图中真实可见的元素，不要编造。

输出 JSON：{"risk_level": "高风险|中风险|低风险|无风险|非楼道", "confidence": 0.0, "hazards": [], "evidence": [], "suggestion": ""}"""


@app.get("/")
def index():
    return FileResponse("index.html")


# File 标明来源：这个参数从 multipart/form-data 的文件部分取，不是查询参数、不是 JSON 字段、不是普通表单字段
# ... 标明必填：请求里没带就返回 422
@app.post("/api/analyze")
async def analyze(file: UploadFile = File(...)):
    try:
        # file 是 UploadFile 对象，.read() 是它的方法，读出文件全部内容。因为底层是异步 IO，所以要 await 等它读完。
        img = Image.open(io.BytesIO(await file.read()))
    except Exception:
        return JSONResponse({"error": "无法解析图片"}, status_code=400)

    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((1280, 1280))  # 把图片原地缩小到指定尺寸以内，保持长宽比，只缩不放
    buf = io.BytesIO()  # 创建一个空的二进制缓冲区
    img.save(buf, "JPEG", quality=85)  # 把图片写进内存缓冲区buf

    t0 = time.perf_counter()
    resp = client.chat.completions.create(
        model=MODEL,
        max_tokens=2000,
        messages=[
            {"role": "system", "content": "你是住宅楼道消防排查专家，只依据图中可见内容判断，只输出 JSON。"},
            {"role": "user", "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url",
                 # .decode() 从 bytes 变成 str
                 "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()}},
            ]},
        ],
    )
    """
    data:image/jpeg;base64,/9j/4AAQSkZJ...
    └┬─┘ └───┬────┘ └──┬─┘ └─────┬─────┘
     │       │         │         └─ 图片的 base64 数据
     │       │         └─ 编码方式，声明后面是 base64
     │       └─ MIME 类型，必须和真实格式一致
     └─ 固定前缀，表示"数据内联在这个字符串里，不用去下载"

    """

    text = resp.choices[0].message.content
    try:
        # 预防返回格式有前缀
        data = json.loads(text[text.find("{"): text.rfind("}") + 1])
    except Exception:
        return {"risk_level": "识别失败", "confidence": 0, "hazards": [],
                "evidence": [], "suggestion": "模型未返回有效 JSON"}

    data["latency_ms"] = round((time.perf_counter() - t0) * 1000)
    return data
