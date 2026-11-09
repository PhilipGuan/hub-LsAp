"""调用 Qwen（OpenAI 兼容接口）并解析结果"""
import json
import re

from openai import OpenAI

from .config import OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL, REQUEST_TIMEOUT
from .prompts import FALLBACK_REASON, FALLBACK_SUGGESTION, SYSTEM_PROMPT, USER_PROMPT

VALID_LEVELS = {"high", "medium", "low", "none", "not_corridor"}

RISK_NAMES = {
    "high": "高风险",
    "medium": "中风险",
    "low": "低风险",
    "none": "无风险",
    "not_corridor": "非楼道",
    "unknown": "识别失败",
}

_client = OpenAI(
    api_key=OPENAI_API_KEY,
    base_url=OPENAI_BASE_URL,
    timeout=REQUEST_TIMEOUT,
    max_retries=1,
)


class LLMError(Exception):
    """模型调用失败"""


def analyze_image(data_url: str) -> dict:
    """返回统一结构的判定结果，不抛业务外的异常"""
    try:
        completion = _client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_url}},
                        {"type": "text", "text": USER_PROMPT},
                    ],
                },
            ],
            temperature=0.1,
            max_tokens=512,
        )
    except Exception as exc:
        raise LLMError(str(exc))

    content = (completion.choices[0].message.content or "").strip()
    return parse_response(content)


def parse_response(content: str) -> dict:
    """解析模型输出，尽量不失败"""
    obj = _loads_lenient(content)

    level = str(obj.get("risk_level", "")).strip().lower()
    if level not in VALID_LEVELS:
        level = _regex_level(content)

    if level is None:
        return {
            "risk_level": "unknown",
            "risk_name": RISK_NAMES["unknown"],
            "objects": [],
            "reason": FALLBACK_REASON,
            "suggestion": FALLBACK_SUGGESTION,
            "raw": content,
        }

    objects = obj.get("objects") or []
    if not isinstance(objects, list):
        objects = [str(objects)]
    objects = [str(o).strip() for o in objects if str(o).strip()][:5]

    return {
        "risk_level": level,
        "risk_name": str(obj.get("risk_name") or RISK_NAMES[level]).strip(),
        "objects": objects,
        "reason": str(obj.get("reason") or "").strip(),
        "suggestion": str(obj.get("suggestion") or "").strip(),
        "raw": content,
    }


def _loads_lenient(content: str) -> dict:
    """去掉代码块包裹 / 前后废话后再解析"""
    text = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.MULTILINE).strip()
    candidates = [text]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])

    for candidate in candidates:
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, TypeError):
            continue
    return {}


def _regex_level(content: str):
    match = re.search(r'"?risk_level"?\s*[:：]\s*"?([a-zA-Z_]+)', content)
    if match and match.group(1).lower() in VALID_LEVELS:
        return match.group(1).lower()
    return None
