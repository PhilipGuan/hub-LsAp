"""DeepSeek 多模态（deepseek-flash）视觉识别客户端，OpenAI 兼容接口。

图片以 base64 data URL 形式放入 content 数组发送。
"""
import base64
import json
import re

import requests

import config
from prompt import SYSTEM_PROMPT, USER_PROMPT


def analyze_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> dict:
    """识别一张图片的楼道消防隐患，返回结构化结果。"""
    if not config.DEEPSEEK_API_KEY:
        raise RuntimeError("未配置 DEEPSEEK_API_KEY，请在 backend/.env 中填写后重启服务。")

    data_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
    payload = {
        "model": config.DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": USER_PROMPT},
                ],
            },
        ],
        "temperature": 0.2,
        "max_tokens": 512,
    }

    resp = requests.post(
        f"{config.DEEPSEEK_BASE_URL}/chat/completions",
        headers={
            "Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=120,
    )

    if resp.status_code != 200:
        raise RuntimeError(f"DeepSeek 接口返回 {resp.status_code}: {resp.text[:500]}")

    content = resp.json()["choices"][0]["message"]["content"]
    return _parse_result(content)


def _parse_result(content: str) -> dict:
    """把模型输出解析成结构化结果，容忍 Markdown 代码块等噪声。"""
    text = (content or "").strip()
    # 去掉 ```json ... ``` 包裹
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    obj = None
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                obj = json.loads(m.group(0))
            except json.JSONDecodeError:
                obj = None

    if obj is None:
        return {
            "risk_level": _guess_level(content),
            "risk_code": _guess_code(content),
            "summary": "",
            "reason": content,
            "raw": content,
        }

    risk_level = str(obj.get("risk_level", "")).strip()
    risk_code = str(obj.get("risk_code", "")).strip() or _guess_code(risk_level)
    return {
        "risk_level": risk_level or _guess_level(risk_code),
        "risk_code": risk_code or _guess_code(risk_level),
        "summary": str(obj.get("summary", "")).strip(),
        "reason": str(obj.get("reason", "")).strip(),
        "raw": content,
    }


_LEVEL_MAP = {
    "high": "高风险",
    "medium": "中风险",
    "low": "低风险",
    "none": "无风险",
    "non_corridor": "非楼道",
}


def _guess_code(text: str) -> str:
    if "非楼道" in text or "无关" in text:
        return "non_corridor"
    if "高风险" in text:
        return "high"
    if "中风险" in text or "中等" in text:
        return "medium"
    if "低风险" in text:
        return "low"
    if "无风险" in text or "无隐患" in text:
        return "none"
    return "unknown"


def _guess_level(text: str) -> str:
    return _LEVEL_MAP.get(_guess_code(text), "未知")
