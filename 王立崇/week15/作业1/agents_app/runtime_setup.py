"""OpenAI Agents SDK 的全局配置。

每个进程都会导入本模块，因此每个进程需要各配置一次：
- 支持自定义 base_url（DeepSeek / Qwen / 本地 vLLM 等）；
- 强制走 Chat Completions 接口；
- 关闭 tracing，避免把对话内容上传到 OpenAI 平台。
"""
from __future__ import annotations

from agents import set_default_openai_api, set_default_openai_client, set_tracing_disabled
from openai import AsyncOpenAI

from shared.config import get_settings

_configured = False


def configure_agents() -> None:
    global _configured
    if _configured:
        return

    set_tracing_disabled(True)

    settings = get_settings()
    if settings.llm_enabled:
        client = AsyncOpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url,
            timeout=settings.timeout,
            max_retries=1,
        )
        set_default_openai_client(client, use_for_tracing=False)
        # 国产模型与大部分兼容服务只实现了 Chat Completions
        set_default_openai_api("chat_completions")

    _configured = True
