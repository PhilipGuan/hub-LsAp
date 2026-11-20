"""大模型客户端（OpenAI 兼容协议）。

* 文本模型与视觉模型共用一套配置，``LLM_VISION_MODEL`` 不填时回落 ``LLM_MODEL``；
* 图片统一转成 data URL（按最长边压缩），既能给在线接口用，也方便离线调试；
* ``LLM_MOCK=1`` 时完全不访问网络，由 :mod:`app.rag.answer` 生成抽取式演示答案，
  便于在没有 API Key 的环境里验证「上传 → Kafka → 解析 → 检索 → 图文回答」全链路。
"""
from __future__ import annotations

import base64
import io
import json
import logging
from pathlib import Path
from typing import Any

from ..config import settings

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    """带用户可读中文信息的模型调用异常。"""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def ready() -> bool:
    return settings.llm_mock or settings.llm_ready


def _client():
    from openai import OpenAI

    return OpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        timeout=settings.llm_timeout,
        max_retries=1,
    )


def _extra_body() -> dict[str, Any]:
    extra: dict[str, Any] = {}
    if settings.llm_extra_body:
        try:
            parsed = json.loads(settings.llm_extra_body)
            if isinstance(parsed, dict):
                extra.update(parsed)
        except ValueError:
            logger.warning("LLM_EXTRA_BODY 不是合法 JSON，已忽略")
    if settings.llm_provider == "qwen" and settings.llm_model.lower().startswith("qwen3"):
        # Qwen3 系列默认开启思考，问答场景关掉更快也更省 token
        extra.setdefault("enable_thinking", False)
    return extra


def _extract_text(message: Any) -> str:
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or ""))
            else:
                parts.append(str(getattr(item, "text", "") or ""))
        return "".join(parts).strip()
    return ""


def to_data_url(source: Path | bytes, *, max_side: int | None = None) -> str:
    """把图片文件/字节转成 JPEG data URL，并按最长边压缩。"""
    limit = max(320, max_side or settings.rag_image_max_side)
    try:
        from PIL import Image
    except ModuleNotFoundError:  # pragma: no cover - 依赖缺失
        if isinstance(source, Path):
            raw = source.read_bytes()
            suffix = source.suffix.lower().lstrip(".") or "png"
            mime = "image/jpeg" if suffix in {"jpg", "jpeg"} else f"image/{suffix}"
            return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")
        return "data:image/jpeg;base64," + base64.b64encode(source).decode("ascii")

    if isinstance(source, Path):
        image = Image.open(source)
    else:
        image = Image.open(io.BytesIO(source))
    with image:
        if image.mode not in {"RGB", "L"}:
            image = image.convert("RGB")
        width, height = image.size
        longest = max(width, height)
        if longest > limit:
            ratio = limit / longest
            image = image.resize((max(1, int(width * ratio)), max(1, int(height * ratio))))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=86, optimize=True)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return "data:image/jpeg;base64," + encoded


def _chat(messages: list[dict[str, Any]], *, model: str, max_tokens: int | None = None) -> str:
    if settings.llm_mock:
        raise LLMError("当前是离线演示模式（LLM_MOCK=1），不会调用真实模型", status_code=409)
    if not settings.llm_ready:
        raise LLMError(
            "尚未配置大模型：请在 .env 中填写 LLM_API_KEY（或 QWEN_API_KEY），"
            "或设置 LLM_MOCK=1 使用离线演示模式。",
            status_code=503,
        )

    from openai import (
        APIConnectionError,
        APIStatusError,
        APITimeoutError,
        AuthenticationError,
        BadRequestError,
        RateLimitError,
    )

    try:
        client = _client()
    except Exception as exc:  # noqa: BLE001 - SDK 初始化失败
        raise LLMError(f"初始化模型客户端失败：{exc}") from exc

    params: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": settings.llm_temperature,
        "max_tokens": max_tokens or settings.llm_max_tokens,
    }
    extra = _extra_body()
    if extra:
        params["extra_body"] = extra

    try:
        try:
            completion = client.chat.completions.create(**params)
        except BadRequestError:
            # 有些网关不接受 extra_body / max_tokens 之外的参数，退化为最小参数集
            fallback = {
                "model": model,
                "messages": messages,
                "temperature": settings.llm_temperature,
            }
            completion = client.chat.completions.create(**fallback)
    except AuthenticationError as exc:
        raise LLMError("模型鉴权失败，请检查 .env 中的 API Key。", status_code=502) from exc
    except RateLimitError as exc:
        raise LLMError("模型调用过于频繁或额度不足，请稍后重试。", status_code=429) from exc
    except APITimeoutError as exc:
        raise LLMError("模型响应超时，请稍后重试或调大 LLM_TIMEOUT。", status_code=504) from exc
    except APIConnectionError as exc:
        raise LLMError(f"无法连接模型服务（{settings.llm_base_url}），请检查网络。", status_code=502) from exc
    except APIStatusError as exc:
        raise LLMError(
            f"模型服务返回 HTTP {exc.status_code}，请检查模型名称与服务状态。", status_code=502
        ) from exc
    except Exception as exc:  # noqa: BLE001 - 兜底，避免 500
        raise LLMError(f"调用模型失败：{exc}") from exc
    finally:
        try:
            client.close()
        except Exception as exc:  # noqa: BLE001
            logger.debug("关闭模型客户端失败: %s", exc)

    if not completion.choices:
        raise LLMError("模型没有返回任何内容，请重试。")
    text = _extract_text(completion.choices[0].message)
    if not text:
        raise LLMError("模型返回了空内容，请重试。")
    return text


def chat(messages: list[dict[str, Any]], *, model: str | None = None) -> str:
    """调用文本/多模态对话接口，返回纯文本。"""
    return _chat(messages, model=model or settings.llm_model)


def describe_image(data_url: str, hint: str = "") -> str:
    """让视觉模型用一句话描述图片，用于「图问」时的检索查询扩展。"""
    if settings.llm_mock or not settings.llm_ready or not settings.vision_enabled:
        return ""
    prompt = (
        "请用一句中文描述这张图片的主要内容，并列出图中出现的关键文字/数字，"
        "总长不超过 80 字，只输出描述本身。"
    )
    if hint:
        prompt += f"\n用户的问题背景：{hint[:200]}"
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": data_url}},
                {"type": "text", "text": prompt},
            ],
        }
    ]
    try:
        return _chat(messages, model=settings.llm_vision_model or settings.llm_model, max_tokens=200)
    except LLMError as exc:
        logger.warning("图片描述失败，仅用文本检索：%s", exc.message)
        return ""


def status() -> dict[str, Any]:
    return {
        "provider": settings.llm_provider,
        "provider_label": settings.provider_label,
        "model": settings.llm_model,
        "vision_model": settings.llm_vision_model,
        "vision_enabled": settings.vision_enabled,
        "ready": settings.llm_ready,
        "mock": settings.llm_mock,
    }
