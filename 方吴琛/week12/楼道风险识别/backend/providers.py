"""多模态供应商封装：DeepSeek Flash V4.1 / Qwen-VL。

两者均提供 OpenAI 兼容的 chat/completions 接口，图片以 base64 的
`image_url` 形式放在 user 消息里，返回文本再由 parse_risk 解析成结构化的
风险结论（风险等级 / 严重程度 / 判断依据）。
"""
from __future__ import annotations

import base64
import json
import re
from typing import Any

import requests

from config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    DASHSCOPE_API_KEY,
    DASHSCOPE_BASE_URL,
    DASHSCOPE_MODEL,
)

SYSTEM_PROMPT = "你是一名专业的消防安全隐患排查专家，擅长识别楼道中的火灾风险。"

# 与案例 notebook 一致的分类标准
USER_PROMPT = """你是一个专业消防专家，擅长识别各种风险。请帮我识别消防安全隐患以及隐患的危险程度。根据楼道中是否存在大量堆积物，堆积物是否可燃以及是否有起火风险将隐患分为无隐患、低风险、中等风险、高风险。

- 高风险：楼道中出现电动车、电瓶、飞线充电等可能起火的元素。
- 中风险：楼道中存在大量堆积物严重影响通行或堆放大量纸箱、木质家具等能造成火势蔓延的堵塞物。
- 低风险：存在楼道堆物现象但不严重。
- 无风险：楼道干净，无堆放物品。
- 非楼道：一些与楼道无关的图片。

请只输出一个 JSON 对象，不要输出任何多余的文字或代码块，格式如下：
{"risk": "高风险", "reason": "简要说明判断依据"}

其中 risk 字段只能是以下五个值之一：高风险、中风险、低风险、无风险、非楼道。"""

PROVIDERS: dict[str, dict[str, str]] = {
    "deepseek": {
        "name": "DeepSeek Flash V4.1",
        "api_key": DEEPSEEK_API_KEY,
        "url": DEEPSEEK_BASE_URL,
        "model": DEEPSEEK_MODEL,
    },
    "qwen": {
        "name": "Qwen-VL",
        "api_key": DASHSCOPE_API_KEY,
        "url": DASHSCOPE_BASE_URL,
        "model": DASHSCOPE_MODEL,
    },
}

# 风险等级 -> 内部标识 / 严重程度 / 展示颜色
RISK_META: dict[str, dict[str, Any]] = {
    "高风险": {"level": "high", "severity": 4, "color": "#dc2626"},
    "中风险": {"level": "medium", "severity": 3, "color": "#ea580c"},
    "低风险": {"level": "low", "severity": 2, "color": "#d97706"},
    "无风险": {"level": "none", "severity": 1, "color": "#16a34a"},
    "非楼道": {"level": "not_corridor", "severity": 0, "color": "#64748b"},
}

# 别名归一化：模型偶尔会输出“中等风险”“无隐患”等说法
_ALIASES: dict[str, list[str]] = {
    "高风险": ["高风险"],
    "中风险": ["中风险", "中等风险", "中危"],
    "低风险": ["低风险", "低危"],
    "无风险": ["无风险", "无隐患", "无堆放", "安全", "干净"],
    "非楼道": ["非楼道", "非走廊", "无关", "不是楼道", "室内", "室外"],
}


def _extract_json(text: str) -> dict | None:
    """从模型输出中尽力提取 JSON 对象（容忍 ```json 包裹、前后缀文字）。"""
    s = text.strip()
    if s.startswith("```"):
        parts = s.split("```")
        # 找第一个非空代码块内容
        for p in parts:
            p = p.strip()
            if p.startswith("json"):
                p = p[4:].strip()
            if p:
                s = p
                break
    start = s.find("{")
    end = s.rfind("}")
    if start != -1 and end > start:
        s = s[start : end + 1]
    try:
        data = json.loads(s)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _normalize_risk(text: str) -> str | None:
    for canon, aliases in _ALIASES.items():
        for alias in aliases:
            if alias in text:
                return canon
    return None


def parse_risk(raw: str) -> dict[str, Any]:
    """把模型原始输出解析为结构化风险结论。"""
    text = raw.strip()
    risk: str | None = None
    reason = ""

    data = _extract_json(text)
    if data:
        for key in ("risk", "风险", "风险等级", "等级", "level", "label", "type"):
            if data.get(key):
                risk = str(data[key]).strip()
                break
        for key in ("reason", "原因", "依据", "说明", "描述", "description"):
            if data.get(key):
                reason = str(data[key]).strip()
                break

    # 兜底：直接关键词匹配
    if not risk:
        risk = _normalize_risk(text)

    # 二次归一化（模型可能输出“中等风险”等别名）
    if risk and risk not in RISK_META:
        risk = _normalize_risk(risk) or risk

    if risk in RISK_META:
        meta = RISK_META[risk]
    else:
        meta = {"level": "unknown", "severity": -1, "color": "#6b7280"}
        risk = risk or "未知"

    return {
        "risk": risk,
        "level": meta["level"],
        "severity": meta["severity"],
        "color": meta["color"],
        "reason": reason,
    }


def analyze_image(image_bytes: bytes, mime_type: str, provider: str = "qwen") -> dict[str, Any]:
    """把图片发给多模态大模型，返回结构化风险结论 + 原始输出。"""
    if provider not in PROVIDERS:
        raise ValueError(f"未知供应商: {provider}")

    cfg = PROVIDERS[provider]
    if not cfg["api_key"]:
        raise RuntimeError(
            f"未配置 {cfg['name']} 的 API Key，请在 backend/.env 中填写对应的 Key"
        )

    b64 = base64.b64encode(image_bytes).decode("utf-8")
    data_url = f"data:{mime_type};base64,{b64}"

    payload = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": USER_PROMPT},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
        "temperature": 0.1,
        "max_tokens": 512,
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
    }

    resp = requests.post(cfg["url"], headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    raw = data["choices"][0]["message"]["content"].strip()

    result = parse_risk(raw)
    result["provider"] = provider
    result["provider_name"] = cfg["name"]
    result["model"] = cfg["model"]
    result["raw"] = raw
    return result
