import base64
import json
import os
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from backend.schemas import RiskAssessment


SYSTEM_PROMPT = """你是楼道消防安全图片识别助手。只根据照片中清晰可见的内容判断，不猜测被遮挡或看不清的物品。

请区分场景和风险：
- 非楼道：照片与楼道场景无关，scene 必须为 non_hallway，risk_level 必须为 null。
- 高风险：楼道中出现电动车、电瓶、飞线充电等可能引发火灾的元素。
- 中等风险：大量堆积物严重影响通行，或有大量纸箱、木质家具等可燃物，容易阻碍逃生或助长火势。
- 低风险：楼道有少量堆物，但没有严重影响通行，也没有明显高危起火元素。
- 无隐患：楼道干净，没有明显堆放物品。

若多个等级同时符合，采用较高等级。场景是楼道时，scene 必须为 hallway，risk_level 只能是 none、low、medium、high 之一。请只返回合法 JSON，不要 Markdown 代码围栏或额外文字，格式如下：
{"scene":"hallway","risk_level":"none|low|medium|high","evidence":["简短可见依据"],"recommendation":"简短建议"}
非楼道格式：{"scene":"non_hallway","risk_level":null,"evidence":["场景判断依据"],"recommendation":"请上传楼道照片"}"""


class MiMoError(Exception):
    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        ]
        return "\n".join(parts)
    raise MiMoError("MiMo 返回了无法识别的内容格式。")


def _parse_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)

    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise MiMoError("MiMo 没有返回有效的风险结果，请重试。") from None
        try:
            value = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            raise MiMoError("MiMo 返回的风险结果格式无效，请重试。") from None

    if not isinstance(value, dict):
        raise MiMoError("MiMo 返回的风险结果格式无效，请重试。")
    return value


def _normalize_result(value: dict[str, Any]) -> RiskAssessment:
    scene_value = str(value.get("scene", "")).strip().lower()
    if scene_value in {"hallway", "corridor", "楼道", "走廊"}:
        scene = "hallway"
    elif scene_value in {"non_hallway", "non-hallway", "not_hallway", "非楼道"}:
        scene = "non_hallway"
    else:
        raise MiMoError("MiMo 返回的场景类型无效，请重试。")

    raw_level = value.get("risk_level", value.get("level"))
    level_map = {
        "none": "none",
        "no_risk": "none",
        "无隐患": "none",
        "无风险": "none",
        "low": "low",
        "低风险": "low",
        "medium": "medium",
        "中风险": "medium",
        "中等风险": "medium",
        "high": "high",
        "高风险": "high",
    }
    risk_level = None if scene == "non_hallway" else level_map.get(str(raw_level).strip().lower())
    if scene == "hallway" and risk_level is None:
        raise MiMoError("MiMo 返回的风险等级无效，请重试。")

    evidence_value = value.get("evidence", [])
    if isinstance(evidence_value, str):
        evidence = [evidence_value]
    elif isinstance(evidence_value, list):
        evidence = [str(item).strip() for item in evidence_value if str(item).strip()]
    else:
        evidence = []

    recommendation = value.get("recommendation", "")
    if not isinstance(recommendation, str):
        recommendation = str(recommendation)

    return RiskAssessment(
        scene=scene,
        risk_level=risk_level,
        evidence=evidence[:4],
        recommendation=recommendation.strip()[:500],
    )


def analyze_image(image_bytes: bytes, mime_type: str) -> RiskAssessment:
    api_key = os.getenv("MIMO_API_KEY", "").strip()
    if not api_key:
        raise MiMoError("尚未配置 MiMo API Key。请在项目 .env 文件中设置 MIMO_API_KEY。", 503)

    base_url = os.getenv("MIMO_BASE_URL", "https://api.xiaomimimo.com/v1").rstrip("/")
    model = os.getenv("MIMO_MODEL", "mimo-v2.6-pro").strip()
    data_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": "请按规定的 JSON 格式识别这张照片中的楼道消防风险。"},
                ],
            },
        ],
        "max_completion_tokens": 512,
        "temperature": 0,
    }
    request = Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=45) as response:
            response_data = json.loads(response.read(2_000_000).decode("utf-8"))
    except HTTPError as exc:
        if exc.code in {401, 403}:
            raise MiMoError("MiMo API 鉴权失败，请检查 MIMO_API_KEY。", 502) from None
        if exc.code == 429:
            raise MiMoError("MiMo API 当前请求较多，请稍后重试。", 503) from None
        raise MiMoError(f"MiMo API 请求失败（HTTP {exc.code}）。", 502) from None
    except (TimeoutError, URLError):
        raise MiMoError("连接 MiMo API 超时或失败，请检查网络后重试。", 504) from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise MiMoError("MiMo API 返回了无效响应，请稍后重试。", 502) from None

    try:
        message = response_data["choices"][0]["message"]
        content = _content_text(message.get("content"))
    except (KeyError, IndexError, TypeError):
        raise MiMoError("MiMo API 响应缺少识别结果，请稍后重试。", 502) from None

    return _normalize_result(_parse_json(content))
