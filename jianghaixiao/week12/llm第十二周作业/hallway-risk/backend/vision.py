"""多模态识别：图片压缩 → base64 → 两阶段调用 → 结构化结果。

当前为架构骨架，需要你补全 TODO。
"""
import base64
import io
import json
import re

from openai import OpenAI
from PIL import Image

from config import (
    OPENAI_API_KEY, OPENAI_BASE_URL, MODEL_NAME,
    MAX_IMAGE_SIDE, JPEG_QUALITY,
)
from prompts import CORRIDOR_PROMPT, RISK_PROMPT, USER_PROMPT
from schemas import RiskAssessment

client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)


# ---------------------------------------------------------------- 工具函数
def compress_image(image_bytes: bytes) -> bytes:
    """压缩图片：转 RGB → 等比缩放到长边 ≤ MAX_IMAGE_SIDE → JPEG。

    TODO:
    1. Image.open(io.BytesIO(image_bytes))
    2. img.convert("RGB")
    3. img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    4. 保存到 BytesIO，format="JPEG", quality=JPEG_QUALITY
    5. 返回 bytes
    """
    raise NotImplementedError("TODO: 实现图片压缩")


def to_data_url(jpeg_bytes: bytes) -> str:
    """把 JPEG 字节转成 data URL。"""
    b64 = base64.b64encode(jpeg_bytes).decode("utf-8")
    return f"data:image/jpeg;base64,{b64}"


def extract_json(text: str) -> dict:
    """从模型输出里抠出 JSON（防止带 markdown 围栏或多余解释）。"""
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError(f"模型没有返回 JSON: {text[:200]}")
    return json.loads(match.group())


# ---------------------------------------------------------------- 模型调用
def _chat_with_image(system_prompt: str, image_bytes: bytes) -> str:
    """发送一条多模态消息，返回模型文本。

    TODO:
    1. compressed = compress_image(image_bytes)
    2. data_url = to_data_url(compressed)
    3. 组装 messages：
       [
         {"role": "system", "content": system_prompt},
         {"role": "user", "content": [
             {"type": "image_url", "image_url": {"url": data_url}},
             {"type": "text", "text": USER_PROMPT},
         ]},
       ]
    4. client.chat.completions.create(
           model=MODEL_NAME,
           messages=messages,
           temperature=0.1,
           max_tokens=800,
       )
    5. 返回 resp.choices[0].message.content
    """
    raise NotImplementedError("TODO: 调用多模态模型")


# ---------------------------------------------------------------- 两阶段判定
def is_corridor(image_bytes: bytes) -> tuple[bool, str]:
    """第一阶段：判断是否楼道。

    TODO:
    1. text = _chat_with_image(CORRIDOR_PROMPT, image_bytes)
    2. data = extract_json(text)
    3. 返回 (bool(data["is_corridor"]), data.get("reason", ""))
    """
    raise NotImplementedError("TODO: 实现第一阶段")


def assess_risk(image_bytes: bytes) -> RiskAssessment:
    """第二阶段：评估风险等级。

    TODO:
    1. text = _chat_with_image(RISK_PROMPT, image_bytes)
    2. data = extract_json(text)
    3. return RiskAssessment(**data)
    """
    raise NotImplementedError("TODO: 实现第二阶段")


def analyze_image(image_bytes: bytes) -> RiskAssessment:
    """完整流程：第一阶段 → 第二阶段 → Pydantic 校验。

    TODO:
    1. corridor, reason = is_corridor(image_bytes)
    2. 若不是楼道：直接返回 RiskAssessment(
           is_corridor=False,
           risk_level="非楼道",
           risk_items=[],
           reason=reason or "图片内容与楼道无关",
           suggestion="请上传楼道/楼梯间照片",
           confidence=0.95,
       )
    3. 若是楼道：return assess_risk(image_bytes)
    """
    raise NotImplementedError("TODO: 实现完整分析流程")