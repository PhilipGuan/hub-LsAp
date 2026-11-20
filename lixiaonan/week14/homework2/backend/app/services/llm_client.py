"""多模态 LLM 客户端（F3-4）：OpenAI 兼容协议封装 + Mock 桩实现。

- openai 后端：任何 OpenAI 兼容服务均可（智谱 paas/v4、dashscope compatible-mode/v1、vLLM 等），
  通过 LLM_API_BASE / LLM_API_KEY / LLM_MODEL 配置
- mock 后端：无 Key 联调用，回答由 chat_service 基于检索结果生成（保证 SSE/前端链路可验证）
"""
import asyncio
from abc import ABC, abstractmethod
from typing import AsyncIterator

from loguru import logger

from app.core.config import Settings


class LLMError(Exception):
    """LLM 调用失败。"""


class BaseLLMClient(ABC):
    @abstractmethod
    def stream_chat(self, messages: list[dict]) -> AsyncIterator[str]:
        """流式生成回答，逐段 yield 文本增量。messages 为 OpenAI 多模态格式。"""


class OpenAICompatClient(BaseLLMClient):
    def __init__(self, settings: Settings):
        from openai import AsyncOpenAI  # 延迟导入

        self.settings = settings
        self.client = AsyncOpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_api_base,
        )

    async def stream_chat(self, messages: list[dict]) -> AsyncIterator[str]:
        extra_body = (
            {"thinking": {"type": "enabled", "effort": self.settings.llm_thinking_effort}}
            if self.settings.llm_thinking_effort else {}
        )
        try:
            stream = await self.client.chat.completions.create(
                model=self.settings.llm_model,
                messages=messages,
                temperature=self.settings.llm_temperature,
                max_tokens=self.settings.llm_max_tokens,
                stream=True,
                extra_body=extra_body,
            )
        except Exception as exc:
            raise LLMError(f"LLM 请求失败（model={self.settings.llm_model}）: {exc}") from exc
        try:
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                content = getattr(delta, "content", None)
                if content:
                    yield content
        except Exception as exc:
            raise LLMError(f"LLM 流式读取中断: {exc}") from exc


class MockLLMClient(BaseLLMClient):
    """无 Key 联调桩：逐段回放调用方传入的预设文本。"""

    def __init__(self, settings: Settings):
        self.settings = settings

    async def stream_chat(self, messages: list[dict]) -> AsyncIterator[str]:
        preset = getattr(self, "_preset_text", "") or "（Mock 模式）已收到问题。"
        for i in range(0, len(preset), 24):
            await asyncio.sleep(0.03)
            yield preset[i:i + 24]


def get_llm_client(settings: Settings) -> BaseLLMClient:
    backend = settings.llm_backend.lower().strip()
    if backend == "openai":
        if not settings.llm_api_key:
            raise LLMError("LLM_BACKEND=openai 但未配置 LLM_API_KEY")
        return OpenAICompatClient(settings)
    if backend == "mock":
        return MockLLMClient(settings)
    raise LLMError(f"未知 LLM 后端: {settings.llm_backend}（可选 openai / mock）")
