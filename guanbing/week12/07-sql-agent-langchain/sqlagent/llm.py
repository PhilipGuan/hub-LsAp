"""模型接入层：原实现 `chat()` 的 LangChain 等价物。

这是整次重写**最核心的一处替换**。对照表：

| 原实现（裸 OpenAI SDK） | 本实现（LangChain） |
|---|---|
| `OpenAI(api_key=..., base_url=...)` | `ChatOpenAI(model=..., base_url=..., api_key=...)` |
| 手写 `for _ in range(3): try: ...` 重试 | `model.with_retry(stop_after_attempt=3)`（框架内建） |
| `messages=[{"role": "system"...}]` 手工拼字典 | `ChatPromptTemplate` + `HumanMessage` 消息对象 |
| `response_format={"type": "json_object"}` + `json.loads` + 手写兜底 | `with_structured_output(Pydantic)` 直接拿到校验过的对象 |
| 返回值 `resp.choices[0].message.content` | `resp.content` |

**刻意不换的东西**：重试次数、`temperature=0.0`、提示词正文、JSON 模式下「解析失败要兜底」的语义。
换框架不是换行为。
"""

from __future__ import annotations

import os
import re
from typing import Sequence

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from .config import LLMSettings, get_llm_settings

# `with_structured_output` 的取数方式：
#   json_mode        —— 设 response_format={"type":"json_object"}，与原实现走同一条传输路径
#   function_calling —— 走工具调用（tool calling）
# 默认与原文一致；可用环境变量覆盖，便于按后端能力切换。
STRUCTURED_METHOD = os.environ.get("STRUCTURED_OUTPUT_METHOD", "json_mode")

# 与原实现一致的调用硬参数
DEFAULT_TEMPERATURE = 0.0
DEFAULT_ATTEMPTS = 3


def build_raw_model(settings: LLMSettings | None = None, *,
                    temperature: float = DEFAULT_TEMPERATURE,
                    attempts: int = DEFAULT_ATTEMPTS) -> ChatOpenAI:
    """构造**原生** `ChatOpenAI`（不带 `with_retry` 包装）。

    为什么要有这个「裸」版本：`with_retry()` 返回的是 `RunnableRetry`，它**没有**
    `with_structured_output()` / `bind_tools()` 这些模型级能力。所以：
    - 纯文本调用 → 用 `build_chat_model()`（已经套好重试）；
    - 结构化输出 → 在本函数产物上先 `with_structured_output()`，再给结果套 `with_retry()`；
    - `create_agent` → 需要真 `BaseChatModel`，重试交给它的 `max_retries`。

    另外，这里**不再改写 `os.environ`**：原实现要先把 DeepSeek 的 Key 伪装成
    `OPENAI_API_KEY` 才能让 SDK 认账，而 `ChatOpenAI` 允许显式传 `api_key` / `base_url`。
    """
    settings = settings or get_llm_settings()
    return ChatOpenAI(
        model=settings.model,
        base_url=settings.base_url,
        api_key=settings.api_key,
        temperature=temperature,
        max_retries=attempts,
    )


def build_chat_model(settings: LLMSettings | None = None, *, temperature: float = DEFAULT_TEMPERATURE,
                     attempts: int = DEFAULT_ATTEMPTS):
    """构造一个「带内建重试」的 Runnable —— 替代原实现里手写的三次 try 循环。"""
    return build_raw_model(settings, temperature=temperature, attempts=0) \
        .with_retry(stop_after_attempt=attempts)


def invoke_messages(messages: Sequence[BaseMessage], *,
                    temperature: float = DEFAULT_TEMPERATURE,
                    settings: LLMSettings | None = None) -> str:
    """一次同步调用（消息列表入参），返回纯文本。

    `invoke_text()` 是它的「system + user 两个字符串」便捷封装；
    `ChatPromptTemplate.format_messages()` 的输出可直接喂给本函数。
    """
    model = build_chat_model(settings, temperature=temperature)
    resp = model.invoke(list(messages))
    return _content_to_text(resp.content).strip()


def invoke_structured_messages(schema: type[BaseModel], messages: Sequence[BaseMessage], *,
                               temperature: float = DEFAULT_TEMPERATURE,
                               settings: LLMSettings | None = None) -> BaseModel | None:
    """结构化输出（消息列表入参）；解析/校验失败返回 `None`。"""
    structured = build_raw_model(settings, temperature=temperature, attempts=0) \
        .with_structured_output(schema, method=STRUCTURED_METHOD) \
        .with_retry(stop_after_attempt=DEFAULT_ATTEMPTS)
    try:
        return structured.invoke(list(messages))
    except Exception:  # noqa: BLE001 —— 含网络错误与解析/校验失败
        return None


def invoke_text(system: str, user: str, *, temperature: float = DEFAULT_TEMPERATURE,
                settings: LLMSettings | None = None) -> str:
    """一次同步调用，返回纯文本 —— 原 `chat(system, user)` 的等价物。"""
    return invoke_messages(
        [SystemMessage(content=system), HumanMessage(content=user)],
        temperature=temperature, settings=settings,
    )


def invoke_structured(schema: type[BaseModel], system: str, user: str, *,
                      temperature: float = DEFAULT_TEMPERATURE,
                      settings: LLMSettings | None = None) -> BaseModel | None:
    """结构化输出 —— 原 `chat(..., json_mode=True)` + `json.loads` 的等价物。

    解析/校验失败返回 `None`（而不是抛异常），**保留原实现「裁判/守门人输出不可解析时
    要显式降级」的语义**：调用方必须自己决定降级成什么，框架不替它猜。
    """
    return invoke_structured_messages(
        schema, [SystemMessage(content=system), HumanMessage(content=user)],
        temperature=temperature, settings=settings,
    )


def strip_code_fence(text: str) -> str:
    """去掉 ```sql ... ``` 之类的围栏，只留正文（照搬原实现）。"""
    text = (text or "").strip()
    text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _content_to_text(content) -> str:
    """兼容 `content` 为字符串或「内容块列表」两种返回形态。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and "text" in block:
                parts.append(str(block["text"]))
        return "".join(parts)
    return "" if content is None else str(content)


# 公开别名：供 `agents/` 从海量消息对象里提取文本
content_to_text = _content_to_text
