"""多模态大模型调用：图片预处理、请求、JSON 解析与结果校验。

对外只暴露 `analyze_image()`，返回结构化的风险判定结果。
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import time
from typing import Any

import httpx
from PIL import Image, ImageOps

from config import settings

try:  # 可选依赖：支持 iPhone 的 HEIC/HEIF
    from pillow_heif import register_heif_opener

    register_heif_opener()
except Exception:  # pragma: no cover - 未安装时静默降级
    pass


RISK_LEVELS = ("高风险", "中风险", "低风险", "无风险", "非楼道")

# 模型偶尔会换个说法，这里做一次归一化
_RISK_ALIASES = {
    "高风险": "高风险",
    "高危险": "高风险",
    "高危": "高风险",
    "严重": "高风险",
    "中风险": "中风险",
    "中等风险": "中风险",
    "中等": "中风险",
    "一般风险": "中风险",
    "低风险": "低风险",
    "较低风险": "低风险",
    "轻微": "低风险",
    "无风险": "无风险",
    "无隐患": "无风险",
    "安全": "无风险",
    "正常": "无风险",
    "非楼道": "非楼道",
    "不是楼道": "非楼道",
    "无关图片": "非楼道",
}

SYSTEM_PROMPT = """你是一名持证的消防安全检查员，专门负责住宅楼楼道（楼梯间、疏散通道、公共走廊）的隐患排查。
你必须严格依据给定的判定标准做出判断，不臆测画面之外的信息，不因画面模糊而夸大风险。
你只输出 JSON 对象，不输出 Markdown、代码块或任何解释性文字。"""

USER_PROMPT = """请判断这张照片中楼道场景的消防安全隐患等级。

判定标准（从上到下依次匹配，命中即停止）：
1. 非楼道：画面主体不是住宅楼道、楼梯间、公共走廊或疏散通道，例如室内房间、街道、风景、人物自拍、文档截图等。
2. 高风险：楼道内出现电动车、电动自行车、电瓶/蓄电池、电池入户、飞线充电、私拉电线等可能直接起火的元素。
3. 中风险：楼道内存在大量堆积物，严重影响通行；或堆放纸箱、木质家具、泡沫塑料、油漆、酒精等可燃物，一旦起火会迅速蔓延。
4. 低风险：存在楼道堆物现象，但数量少、体积小，基本不影响通行，也没有集中堆放的可燃物。
5. 无风险：楼道干净整洁，没有堆放物品。

输出要求：
- risk_level：只能是 "高风险"、"中风险"、"低风险"、"无风险"、"非楼道" 之一。
- confidence：0 到 1 之间的小数，表示你对本次判定的把握程度。
- is_stairwell：布尔值，画面是否属于住宅楼道场景。
- evidence：字符串数组，1-4 条，逐条列出画面中支持该判定的具体观察（写清楚看到了什么、在哪里），不要空泛。
- suggestion：一条可直接执行的处理建议（非楼道时可写"请上传楼道照片"）。
- 如果画面信息不足，选择更保守的等级，并在 evidence 中说明原因。

只输出如下 JSON：
{"risk_level": "...", "confidence": 0.0, "is_stairwell": true, "evidence": ["..."], "suggestion": "..."}"""


class AnalyzerError(Exception):
    """可安全展示给前端的错误。"""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# --------------------------------------------------------------------------- #
# 图片预处理
# --------------------------------------------------------------------------- #
def prepare_image(data: bytes) -> tuple[bytes, str]:
    """校验并压缩图片，返回 (jpeg_bytes, mime)。

    统一转成 JPEG：去掉 EXIF 方向信息、限制长边、缩小传输体积。
    """
    if not data:
        raise AnalyzerError("上传的图片为空，请重新拍摄或选择图片。", 400)
    if len(data) > settings.max_image_bytes:
        raise AnalyzerError(
            f"图片超过 {settings.max_image_mb}MB，请压缩后重试。", 413
        )

    try:
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            img = ImageOps.exif_transpose(img)
            if img.mode in ("RGBA", "LA", "P"):
                rgba = img.convert("RGBA")
                background = Image.new("RGB", rgba.size, (255, 255, 255))
                background.paste(rgba, mask=rgba.split()[-1])
                img = background
            elif img.mode != "RGB":
                img = img.convert("RGB")

            img.thumbnail(
                (settings.image_max_side, settings.image_max_side), Image.Resampling.LANCZOS
            )
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=settings.jpeg_quality, optimize=True)
            return buffer.getvalue(), "image/jpeg"
    except AnalyzerError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise AnalyzerError("无法解析这张图片，请换一张 JPG/PNG 格式的照片。", 400) from exc


# --------------------------------------------------------------------------- #
# 结果解析与校验
# --------------------------------------------------------------------------- #
def _extract_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            raise AnalyzerError("模型没有返回可解析的结果，请重试。") from None
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise AnalyzerError("模型返回的结果格式异常，请重试。") from exc
    if not isinstance(value, dict):
        raise AnalyzerError("模型返回的结果格式异常，请重试。")
    return value


def _as_confidence(value: Any) -> float:
    if isinstance(value, str):
        value = value.strip().rstrip("%")
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.5
    if number > 1:  # 兼容 0-100 的写法
        number /= 100
    return round(min(max(number, 0.0), 1.0), 2)


def _as_evidence(value: Any) -> list[str]:
    if isinstance(value, str):
        parts = re.split(r"[\n；;]+", value)
    elif isinstance(value, list):
        parts = [str(item) for item in value]
    else:
        parts = []
    items = [part.strip(" -•\t") for part in parts if str(part).strip()]
    return items[:4] or ["画面信息有限，未能提取到明确的隐患特征。"]


def normalize_result(raw: dict[str, Any]) -> dict[str, Any]:
    """把模型输出规整成稳定的结构，非法等级直接报错触发重试。"""
    level = _RISK_ALIASES.get(str(raw.get("risk_level", "")).strip())
    if level is None:
        raise AnalyzerError("模型返回的风险等级不在约定范围内，请重试。")

    is_stairwell = raw.get("is_stairwell")
    if not isinstance(is_stairwell, bool):
        is_stairwell = level != "非楼道"

    # 两处信息冲突时，以风险等级为准，保证展示一致
    if not is_stairwell:
        level = "非楼道"
    elif level == "非楼道":
        is_stairwell = False

    suggestion = str(raw.get("suggestion") or "").strip()
    if not suggestion:
        suggestion = "请上传更清晰的楼道照片后重新识别。"

    return {
        "risk_level": level,
        "confidence": _as_confidence(raw.get("confidence")),
        "is_stairwell": is_stairwell,
        "evidence": _as_evidence(raw.get("evidence")),
        "suggestion": suggestion,
    }


# --------------------------------------------------------------------------- #
# 模型调用
# --------------------------------------------------------------------------- #
def _build_payload(image_b64: str, mime: str, repair_hint: str | None) -> dict[str, Any]:
    text = USER_PROMPT
    if repair_hint:
        text += f"\n\n注意：上一次输出无法解析（{repair_hint}）。请严格只输出一个 JSON 对象。"

    return {
        "model": settings.model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": text},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{image_b64}"},
                    },
                ],
            },
        ],
        "temperature": 0.1,
        "max_tokens": settings.max_output_tokens,
        "response_format": {"type": "json_object"},
    }

# 调用
def _request_once(
    image_b64: str, mime: str, repair_hint: str | None = None
) -> tuple[str, str]:
    """调用一次模型，返回 (content, finish_reason)。"""
    payload = _build_payload(image_b64, mime, repair_hint)
    headers = {
        "Authorization": f"Bearer {settings.api_key}",
        "Content-Type": "application/json",
    }
    url = f"{settings.base_url}/chat/completions"

    try:
        with httpx.Client(timeout=settings.request_timeout) as client:
            response = client.post(url, headers=headers, json=payload)
            # 少数模型不支持 response_format，去掉response_format后重试一次
            if response.status_code == 400 and "response_format" in response.text:
                payload.pop("response_format", None)
                response = client.post(url, headers=headers, json=payload)
    except httpx.TimeoutException as exc:
        raise AnalyzerError("模型响应超时，请稍后重试。", 504) from exc
    except httpx.HTTPError as exc:
        raise AnalyzerError(f"无法连接模型服务：{exc}", 502) from exc

    if response.status_code == 401:
        raise AnalyzerError("模型服务鉴权失败，请检查 DASHSCOPE_API_KEY 是否正确。", 502)
    if response.status_code == 429:
        raise AnalyzerError("模型服务限流，请稍后再试。", 429)
    if response.status_code >= 400:
        detail = _short_error(response)
        raise AnalyzerError(f"模型服务返回错误（{response.status_code}）：{detail}", 502)

    try:
        body = response.json()
        choice = body["choices"][0]
        content = (choice.get("message") or {}).get("content") or ""
        finish_reason = str(choice.get("finish_reason") or "")
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise AnalyzerError("模型返回内容为空或格式异常，请重试。") from exc

    return content, finish_reason


def _short_error(response: httpx.Response) -> str:
    try:
        body = response.json()
        error = body.get("error") or {}
        message = error.get("message") or body.get("message") or response.text
    except ValueError:
        message = response.text
    message = str(message).strip().replace("\n", " ")
    return message[:200] or "未知错误"


#
def analyze_image(data: bytes) -> dict[str, Any]:
    """识别一张楼道照片，返回结构化结果。"""
    # 先校验并压缩：无论真实调用还是演示模式，都保证输入是一张有效图片
    image_bytes, mime = prepare_image(data)

    # 演示模式拦截
    if settings.mock_mode:
        return _mock_result(image_bytes)

    # 鉴权失败拦截
    if not settings.key_configured:
        raise AnalyzerError(
            "尚未配置模型 API Key：请复制 .env.example 为 .env 并填写 DASHSCOPE_API_KEY，"
            "或设置 MOCK_MODE=1 先用演示模式体验。",
            503,
        )

    image_b64 = base64.b64encode(image_bytes).decode("ascii")

    started = time.perf_counter()
    raw_text, finish_reason = _request_once(image_b64, mime)
    # 处理大模型的“思考模式”
    if not raw_text.strip():
        if finish_reason == "length":
            raise AnalyzerError(
                "模型把输出长度都用在了思考过程上，没有给出最终结论。"
                "请重试，或在 .env 中调大 MAX_OUTPUT_TOKENS。",
                502,
            )
        raise AnalyzerError("模型没有返回结果内容，请重试。", 502)

    # 自我修正机制：模型返回的结果可能有问题，先尝试解析，如果解析失败，再给模型一次机会
    try:
        result = normalize_result(_extract_json(raw_text))
    except AnalyzerError as first_error:
        # 给模型一次自我修正的机会
        retry_text, _ = _request_once(image_b64, mime, repair_hint=first_error.message)
        result = normalize_result(_extract_json(retry_text))

    result.update(
        {
            "model": settings.model,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "mock": False,
        }
    )
    return result


# --------------------------------------------------------------------------- #
# 演示模式：没有可用 Key 时用确定性假数据跑通流程
# --------------------------------------------------------------------------- #
_MOCK_TEMPLATES: dict[str, dict[str, Any]] = {
    "高风险": {
        "confidence": 0.93,
        "evidence": [
            "楼道内停放一辆电动自行车，占用疏散通道",
            "墙面插座处有私拉电线，存在飞线充电迹象",
        ],
        "suggestion": "立即将电动车移出楼道，拆除私拉电线，并张贴禁止入户充电告示。",
    },
    "中风险": {
        "confidence": 0.88,
        "evidence": [
            "楼梯转角堆放多个纸箱和木质柜子，占用约一半通道宽度",
            "堆积物以可燃材料为主，起火后易阻断疏散",
        ],
        "suggestion": "当日清空纸箱、木质家具等可燃堆积物，恢复通道净宽。",
    },
    "低风险": {
        "confidence": 0.81,
        "evidence": ["楼道角落放置少量纸袋和一把折叠椅", "堆放物体积较小，不影响正常通行"],
        "suggestion": "提醒住户自行清理，并纳入日常巡查记录。",
    },
    "无风险": {
        "confidence": 0.95,
        "evidence": ["楼梯与走廊地面干净，无堆放物", "疏散通道畅通，未见可燃物或充电设备"],
        "suggestion": "保持现状，按周期开展例行巡查。",
    },
    "非楼道": {
        "confidence": 0.9,
        "evidence": ["画面主体为室内办公场景，未出现楼梯间或公共走廊"],
        "suggestion": "请上传住宅楼道、楼梯间或公共走廊的照片。",
    },
}
_MOCK_ORDER = ("高风险", "中风险", "低风险", "无风险", "非楼道")

# 演示模式生成器
def _mock_result(data: bytes) -> dict[str, Any]:
    digest = hashlib.sha256(data).digest()
    level = _MOCK_ORDER[digest[0] % len(_MOCK_ORDER)]
    template = _MOCK_TEMPLATES[level]
    return {
        "risk_level": level,
        "confidence": template["confidence"],
        "is_stairwell": level != "非楼道",
        "evidence": template["evidence"],
        "suggestion": template["suggestion"],
        "model": "mock",
        "elapsed_ms": 120 + digest[1] % 300,
        "mock": True,
    }
