"""模型调用与无 Key 降级。"""
from __future__ import annotations

import json
import re

import httpx

from . import config, prompts


async def _chat(system: str, user: str) -> str | None:
    if config.mock_mode() or not config.OPENAI_API_KEY:
        return None

    url = config.OPENAI_BASE_URL.rstrip("/") + "/chat/completions"
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {config.OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": config.MODEL_NAME,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.2,
            },
        )
        response.raise_for_status()
        payload = response.json()
    return payload["choices"][0]["message"]["content"]


def _json_from_text(text: str | None) -> dict | None:
    if not text:
        return None
    match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    candidate = match.group(1) if match else text
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


async def generate_keywords(topic: str) -> list[str]:
    text = await _chat(
        prompts.KEYWORD_SYSTEM,
        f"研究主题：{topic}",
    )
    payload = _json_from_text(text)
    if payload and isinstance(payload.get("keywords"), list):
        keywords = [str(item).strip() for item in payload["keywords"] if str(item).strip()]
        if keywords:
            return keywords[:5]

    return [
        topic,
        f"{topic} 代表性方案",
        f"{topic} 对比",
        f"{topic} 趋势",
    ]


async def summarize(topic: str, keyword: str, results: list[dict]) -> str:
    fallback = "\n".join(
        f"{item.get('title', '')}：{item.get('snippet', '')}"
        for item in results
        if item.get("snippet")
    )
    text = await _chat(
        prompts.SUMMARY_SYSTEM,
        json.dumps(
            {"topic": topic, "keyword": keyword, "results": results},
            ensure_ascii=False,
        ),
    )
    return (text or fallback or f"未检索到“{keyword}”的有效资料。").strip()


async def judge(
    topic: str,
    draft_text: str,
    source_count: int,
    searched_keywords: list[str],
    round_no: int,
) -> dict:
    text = await _chat(
        prompts.JUDGE_SYSTEM,
        json.dumps(
            {
                "topic": topic,
                "draft": draft_text,
                "source_count": source_count,
                "searched_keywords": searched_keywords,
                "round": round_no,
            },
            ensure_ascii=False,
        ),
    )
    payload = _json_from_text(text)
    if payload and "sufficient" in payload:
        payload.setdefault("reason", "")
        payload.setdefault("new_keywords", [])
        return payload

    sufficient = source_count >= 8 or round_no >= config.MAX_ROUNDS
    return {
        "sufficient": sufficient,
        "reason": "mock 模式按来源数量判断",
        "new_keywords": [] if sufficient else [f"{topic} 补充资料"],
    }


async def report_metadata(
    topic: str,
    draft_text: str,
    sources: list[dict],
) -> dict:
    text = await _chat(
        prompts.REPORT_SYSTEM,
        json.dumps(
            {"topic": topic, "draft": draft_text, "sources": sources},
            ensure_ascii=False,
        ),
    )
    payload = _json_from_text(text)
    if payload:
        return payload

    source_urls = [item.get("url", "") for item in sources[:2] if item.get("url")]
    return {
        "title": f"{topic}研究报告",
        "summary": draft_text[:500],
        "key_conclusions": [
            {
                "text": f"围绕“{topic}”的主要资料已完成汇总，可继续补充更权威来源。",
                "source_urls": source_urls,
                "is_model_inference": False,
            }
        ],
        "open_questions": ["后续可接入单位内部资料和更长时间范围的数据。"],
    }
