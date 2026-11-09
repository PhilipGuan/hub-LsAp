"""大模型调用封装（OpenAI 兼容协议）。

默认对接阿里云百炼 DashScope，也可以通过环境变量切换到任意兼容服务。
工作流只依赖 ``complete(messages) -> str`` 这一个协议，方便离线测试时注入假模型。
"""

from __future__ import annotations

import random
import time
from typing import Any, Optional, Protocol, Sequence

from .config import Settings

try:  # openai 是唯一的外部依赖
    from openai import (
        APIConnectionError,
        APIStatusError,
        APITimeoutError,
        AuthenticationError,
        BadRequestError,
        OpenAI,
        RateLimitError,
    )
except ImportError:  # pragma: no cover
    OpenAI = None  # type: ignore[assignment]


class LLMError(RuntimeError):
    """大模型调用失败。"""


class LLMProtocol(Protocol):
    def complete(self, messages: Sequence[dict[str, str]]) -> str:
        ...


class LLMClient:
    """带重试与 token 统计的 OpenAI 兼容客户端。"""

    def __init__(self, settings: Settings) -> None:
        settings.require_api_key()
        if OpenAI is None:
            raise LLMError("未安装 openai SDK，请执行：pip install openai")

        self.settings = settings
        self.client = OpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url,
            timeout=settings.timeout,
        )
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0

    def complete(
        self,
        messages: Sequence[dict[str, str]],
        max_retries: int = 3,
        temperature: Optional[float] = None,
    ) -> str:
        """调用模型并返回文本内容。"""
        last_error: Optional[Exception] = None

        for attempt in range(1, max_retries + 1):
            try:
                response = self.client.chat.completions.create(
                    model=self.settings.model,
                    messages=list(messages),
                    temperature=(
                        self.settings.temperature
                        if temperature is None
                        else temperature
                    ),
                )
                self.calls += 1

                usage = getattr(response, "usage", None)
                if usage is not None:
                    self.prompt_tokens += int(getattr(usage, "prompt_tokens", 0) or 0)
                    self.completion_tokens += int(
                        getattr(usage, "completion_tokens", 0) or 0
                    )

                content = response.choices[0].message.content if response.choices else ""
                if not content or not content.strip():
                    raise LLMError("模型返回了空内容")
                return content.strip()

            except AuthenticationError as exc:
                raise LLMError(
                    "大模型鉴权失败，请检查 API Key 是否正确、是否已过期。"
                ) from exc
            except BadRequestError as exc:
                raise LLMError(f"大模型请求参数错误：{exc}") from exc
            except (RateLimitError, APITimeoutError, APIConnectionError) as exc:
                last_error = exc
            except APIStatusError as exc:
                # 5xx 可重试，4xx 直接失败
                if exc.status_code and exc.status_code >= 500:
                    last_error = exc
                else:
                    raise LLMError(f"大模型调用失败：{exc}") from exc
            except Exception as exc:  # noqa: BLE001 - 统一包装，便于 CLI 展示
                last_error = exc

            if attempt < max_retries:
                time.sleep(min(8.0, 1.5 * attempt) + random.random() * 0.5)

        raise LLMError(f"大模型调用失败（已重试 {max_retries} 次）：{last_error}")

    def ask(self, prompt: str, system: str = "你是一个严谨的数据分析助手。") -> str:
        """单轮提问的便捷方法。"""
        return self.complete(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ]
        )

    def usage(self) -> dict[str, Any]:
        return {
            "model": self.settings.model,
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
        }
