"""多模态大模型适配器。

M1 实现 OpenAI 兼容协议（默认指向智谱 GLM-4.1V-Flash）；
后续如需本地 Qwen2.5-VL，可在此增加同签名实现并按配置路由。

流程：解码图片 -> 校验/兜底压缩 -> 调模型 -> 提取 JSON（失败带纠错指令重试一次）
      -> 字段归一化 -> 返回结构化结果。
"""

import base64
import io
import json
import re
import time

import httpx
from PIL import Image, ImageOps

from . import config
from .prompt import RETRY_PROMPT, SYSTEM_PROMPT, USER_PROMPT
from .schemas import RISK_LABELS


class VisionError(Exception):
    """模型调用/解析失败，message 可安全返回给客户端。"""

    def __init__(self, message: str, status_code: int = 503):
        super().__init__(message)
        self.status_code = status_code
        self.raw: str | None = None  # 模型原始输出，用于日志排查


# ---------------------------------------------------------------- 图片处理

def _decode_image(image: str) -> bytes:
    """接受 Data URL 或纯 base64，返回原始字节。"""
    image = image.strip()
    if image.startswith("data:"):
        match = re.match(r"data:image/[\w.+-]+;base64,(.+)", image, re.DOTALL)
        if not match:
            raise VisionError("图片格式错误：仅支持 data:image/...;base64,", status_code=400)
        image = match.group(1)
    try:
        return base64.b64decode(image)
    except Exception as exc:
        raise VisionError("图片 base64 解码失败", status_code=400) from exc


def _normalize_image(data: bytes) -> tuple[str, bytes]:
    """校验图片合法性，超过阈值时兜底压缩，返回 (mime, bytes)。"""
    if len(data) > config.MAX_IMAGE_BYTES:
        raise VisionError(
            f"图片过大（>{config.MAX_IMAGE_BYTES // 1024 // 1024}MB），请压缩后上传",
            status_code=400,
        )
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as exc:
        raise VisionError("无法识别的图片文件", status_code=400) from exc

    img = ImageOps.exif_transpose(img)
    if img.mode != "RGB":
        img = img.convert("RGB")
    if max(img.size) > config.MAX_IMAGE_SIDE:
        img.thumbnail((config.MAX_IMAGE_SIDE, config.MAX_IMAGE_SIDE))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return "image/jpeg", buf.getvalue()


# ---------------------------------------------------------------- 模型调用

def _chat(messages: list[dict]) -> str:
    """调用 OpenAI 兼容的 chat/completions 接口，返回模型文本输出。"""
    if not config.API_KEY:
        raise VisionError(
            "API_KEY 未配置：请在项目根目录 .env 中填写 API_KEY 后重启服务"
        )
    payload = {
        "model": config.MODEL,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": 2048,  # 思考型模型会先消耗部分 token 推理
        "stream": False,
    }
    try:
        resp = httpx.post(
            f"{config.BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {config.API_KEY}"},
            json=payload,
            timeout=config.REQUEST_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise VisionError(f"调用模型网络异常: {exc}") from exc

    if resp.status_code == 401:
        raise VisionError("模型鉴权失败(401)：请检查 API_KEY 是否正确")
    if resp.status_code != 200:
        raise VisionError(f"模型接口异常 HTTP {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content = message.get("content") or message.get("reasoning_content") or ""
    if not content.strip():
        raise VisionError(f"模型返回内容为空: {json.dumps(data, ensure_ascii=False)[:300]}")
    return _strip_think(content)


# ---------------------------------------------------------------- 输出解析

_THINK_RE = re.compile(r"<think>.*?(?:</think>|$)", re.DOTALL)


def _strip_think(text: str) -> str:
    """剥离思考型模型输出中的 <think>...</think> 推理过程（含未闭合截断的情况）。"""
    return _THINK_RE.sub("", text).strip()

_LEVEL_ALIASES = {
    "high": "high",
    "高风险": "high",
    "medium": "medium",
    "mid": "medium",
    "中风险": "medium",
    "low": "low",
    "低风险": "low",
    "none": "none",
    "无风险": "none",
    "safe": "none",
    "not_corridor": "not_corridor",
    "非楼道": "not_corridor",
    "irrelevant": "not_corridor",
}


def _extract_json(text: str) -> dict | None:
    """提取文本中第一个平衡的 {...} 并解析，失败返回 None。"""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _normalize_level(value) -> str:
    key = str(value).strip().lower()
    if key in _LEVEL_ALIASES:
        return _LEVEL_ALIASES[key]
    for alias, level in _LEVEL_ALIASES.items():
        if alias in key:
            return level
    raise ValueError(f"未知的风险等级: {value!r}")


def _normalize_elements(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = re.split(r"[、,，;；/]", value)
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()]


def _clamp_confidence(value) -> float:
    try:
        c = float(value)
    except (TypeError, ValueError):
        return 0.5
    return round(min(max(c, 0.0), 1.0), 2)


def _parse_result(text: str) -> dict:
    """把模型输出解析并归一化为业务字段，任何失败抛 ValueError。"""
    parsed = _extract_json(text)
    if parsed is None:
        raise ValueError(f"输出中未找到可解析的 JSON: {text[:200]}")
    try:
        level = _normalize_level(parsed.get("risk_level", ""))
    except ValueError as exc:
        raise ValueError(f"{exc}; 原始输出: {text[:200]}") from exc
    return {
        "risk_level": level,
        "risk_label": RISK_LABELS[level],
        "confidence": _clamp_confidence(parsed.get("confidence")),
        "elements": _normalize_elements(parsed.get("elements")),
        "reason": str(parsed.get("reason", "")).strip(),
        "suggestion": str(parsed.get("suggestion", "")).strip(),
    }


# ---------------------------------------------------------------- 对外入口

def analyze_image(image: str) -> dict:
    """识别单张图片的楼道消防隐患等级，返回 AnalyzeResponse 对应的字典。"""
    t0 = time.monotonic()
    raw_bytes = _decode_image(image)
    mime, payload = _normalize_image(raw_bytes)
    data_url = f"data:{mime};base64,{base64.b64encode(payload).decode()}"

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": data_url}},
                {"type": "text", "text": USER_PROMPT},
            ],
        },
    ]

    text = _chat(messages)
    try:
        result = _parse_result(text)
    except ValueError:
        # 带纠错指令重试一次
        messages += [
            {"role": "assistant", "content": text},
            {"role": "user", "content": RETRY_PROMPT},
        ]
        text = _chat(messages)
        try:
            result = _parse_result(text)
        except ValueError as exc:
            err = VisionError(f"模型输出无法解析为 JSON: {exc}")
            err.raw = text
            raise err from exc

    result["model"] = config.MODEL
    result["elapsed_ms"] = int((time.monotonic() - t0) * 1000)
    return result
