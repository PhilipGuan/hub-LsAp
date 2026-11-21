"""LangGraph 情感回应节点使用的 MiMo 调用。"""

import os
from collections.abc import Sequence
from json import JSONDecodeError

from openai import APIError, AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from backend.agents._mimo import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    DISABLE_THINKING,
    InvalidCompletionError,
    completion_text,
)
from backend.models.schemas import EmotionResult, HistoryMessage as HistoryMessage


_INSTRUCTIONS = """结合当前消息和同一会话的历史，直接用用户当前使用的语言回应。
正面情绪时，回应用户分享的具体事情，适度鼓励。
负面情绪时，先承接用户的感受，再视情况提供安慰或可行的小建议。
中性情绪时，优先回答用户的问题或自然接话。
把情绪识别结果作为语气参考，以用户实际表达为准。
避免替用户下结论、诊断或淡化感受，也避免透露内部分类标签。"""


class ResponseAgentError(Exception):
    """情感回应执行失败。"""


class ResponseConfigurationError(ResponseAgentError):
    """缺少 MiMo 调用所需的配置。"""


class ResponseAPIError(ResponseAgentError):
    """MiMo API 调用失败。"""


class ResponseOutputError(ResponseAgentError):
    """MiMo 未返回可用的回复文本。"""


async def generate_response(
    message: str,
    emotion: EmotionResult,
    history: Sequence[HistoryMessage] = (),
) -> str:
    """根据按时间顺序传入的历史消息生成本轮完整回复。"""
    if not message.strip():
        raise ValueError("消息内容不能为空")

    instructions = f"{_INSTRUCTIONS}\n本轮情绪倾向为 {emotion.sentiment}，细分情绪为 {emotion.emotion}。"
    messages: list[ChatCompletionMessageParam] = [{"role": "system", "content": instructions}]
    for item in history:
        role = item["role"]
        if role not in ("user", "assistant"):
            raise ValueError("历史消息角色必须是 user 或 assistant")
        messages.append({"role": role, "content": item["content"]})
    messages.append({"role": "user", "content": message})

    api_key = os.getenv("MIMO_API_KEY", "").strip()
    if not api_key:
        raise ResponseConfigurationError("请设置 MIMO_API_KEY")
    base_url = os.getenv("MIMO_BASE_URL", "").strip() or DEFAULT_BASE_URL
    model_id = os.getenv("RESPONSE_MODEL", "").strip() or DEFAULT_MODEL

    try:
        async with AsyncOpenAI(
            api_key=api_key, base_url=base_url, timeout=15.0, max_retries=0
        ) as client:
            completion = await client.chat.completions.create(
                model=model_id, messages=messages, stream=False, extra_body=DISABLE_THINKING
            )
        return completion_text(completion)
    except (APIError, JSONDecodeError) as error:
        raise ResponseAPIError("MiMo API 调用失败") from error
    except InvalidCompletionError as error:
        raise ResponseOutputError("MiMo 未返回可用的回复文本") from error
