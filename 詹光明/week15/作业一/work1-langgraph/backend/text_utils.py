"""消息文本处理的小工具：统一不同 content 形态，压缩提示词长度。"""

from __future__ import annotations

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage


def message_text(message: AnyMessage) -> str:
    """把字符串 / 内容块列表统一转成纯文本。"""

    content = message.content
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts).strip()
    return str(content).strip()


def compact_text(text: str, limit: int) -> str:
    """压平空白并截断，避免单条历史消息撑爆提示词。"""

    compact = " ".join(str(text).split())
    if limit <= 0 or len(compact) <= limit:
        return compact
    return compact[:limit] + "…"


def last_user_text(messages: list[AnyMessage]) -> str:
    """取最后一条用户消息；图状态里可能还混有 AI 消息。"""

    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return message_text(message)
    return ""


def render_history(
    messages: list[AnyMessage],
    max_messages: int,
    max_chars_per_message: int = 500,
) -> str:
    """把最近若干轮对话渲染成提示词里的纯文本。"""

    if max_messages <= 0:
        return "（无）"

    lines: list[str] = []
    for message in messages[-max_messages:]:
        if isinstance(message, HumanMessage):
            role = "用户"
        elif isinstance(message, AIMessage):
            role = "助手"
        else:
            continue
        text = compact_text(message_text(message), max_chars_per_message)
        if text:
            lines.append(f"{role}：{text}")
    return "\n".join(lines) if lines else "（无）"
