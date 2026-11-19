"""MiMo 两个节点共用的模型配置与文本校验。"""

from openai.types.chat import ChatCompletion, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice


DEFAULT_BASE_URL = "https://api.xiaomimimo.com/v1"
DEFAULT_MODEL = "mimo-v2.6-flash"
DISABLE_THINKING = {"thinking": {"type": "disabled"}}


class InvalidCompletionError(Exception):
    """聊天接口没有返回完整的助手文本。"""


def completion_text(completion: ChatCompletion) -> str:
    if not isinstance(completion, ChatCompletion) or not completion.choices:
        raise InvalidCompletionError("MiMo 未返回可用的聊天结果")

    choice = completion.choices[0]
    if (
        not isinstance(choice, Choice)
        or choice.finish_reason != "stop"
        or not isinstance(choice.message, ChatCompletionMessage)
        or choice.message.refusal
    ):
        raise InvalidCompletionError("MiMo 回复不完整或被拒绝")

    content = choice.message.content
    if not isinstance(content, str) or not content.strip():
        raise InvalidCompletionError("MiMo 未返回有效的回复文本")
    return content
