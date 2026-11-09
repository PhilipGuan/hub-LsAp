"""DeepSeek 客户端封装：JSON 输出、空输出重试、异常兜底。"""
from __future__ import annotations

import json
import re
from typing import Any

import logging

import httpx

from backend import config

logger = logging.getLogger(__name__)

_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.S)


class LLMError(RuntimeError):
    pass


def _extract_json(text: str) -> Any:
    """从模型输出里抠出 JSON，容忍 Markdown 代码块和前后废话。"""
    if not text:
        raise LLMError("模型返回空内容")
    text = text.strip()
    blocks = _JSON_BLOCK.findall(text)
    candidates = list(blocks) + [text]
    for cand in candidates:
        cand = cand.strip()
        try:
            return json.loads(cand)
        except json.JSONDecodeError:
            pass
    # 退化：截取第一个 { 到最后一个 }
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    start, end = text.find("["), text.rfind("]")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    raise LLMError(f"无法解析为 JSON：{text[:200]}")


def _client() -> httpx.Client:
    if not config.DEEPSEEK_API_KEY:
        raise LLMError("未配置 DEEPSEEK_API_KEY，请检查项目根目录 .env")
    return httpx.Client(
        base_url=config.DEEPSEEK_BASE_URL,
        headers={
            "Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
        },
        timeout=config.LLM_TIMEOUT,
    )


def chat(
    system: str,
    user: str,
    temperature: float = 0.3,
    max_tokens: int = 4000,
    json_mode: bool = False,
) -> str:
    """普通对话，返回纯文本。失败重试 LLM_MAX_RETRY 次。"""
    payload: dict[str, Any] = {
        "model": config.DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    last_err: Exception | None = None
    for attempt in range(1, config.LLM_MAX_RETRY + 1):
        try:
            with _client() as cli:
                resp = cli.post("/chat/completions", json=payload)
                resp.raise_for_status()
                data = resp.json()
            content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
            if content and content.strip():
                return content.strip()
            last_err = LLMError("模型返回空内容")
            logger.warning("LLM 返回空内容，第 %s/%s 次重试", attempt, config.LLM_MAX_RETRY)
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            logger.warning("LLM 调用失败（%s/%s）：%s", attempt, config.LLM_MAX_RETRY, exc)
    raise LLMError(f"LLM 调用最终失败：{last_err}")


def chat_json(
    system: str,
    user: str,
    temperature: float = 0.2,
    max_tokens: int = 4000,
) -> Any:
    """要求模型输出 JSON 并解析。system 里应说明输出格式。"""
    sys_prompt = system + "\n\n严格只输出 JSON，不要输出任何解释文字、不要使用 Markdown 代码块。"
    raw = chat(sys_prompt, user, temperature=temperature, max_tokens=max_tokens, json_mode=True)
    return _extract_json(raw)


if __name__ == "__main__":
    from backend.config import setup_logging

    setup_logging()
    if not config.DEEPSEEK_API_KEY:
        print("未配置 DEEPSEEK_API_KEY，跳过联网测试")
        print("纯函数自检：")
        print("  _extract_json 代码块 ->", _extract_json('```json\n{"a": 1}\n```'))
        print("  _extract_json 裸 JSON ->", _extract_json('  {"b": [1,2]}  '))
    else:
        out = chat_json(
            "你是助手。",
            '返回一个 JSON：{"name": "深度研究助手", "ok": true}',
        )
        print("chat_json ->", out)
