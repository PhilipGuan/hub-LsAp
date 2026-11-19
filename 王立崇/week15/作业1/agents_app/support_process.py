"""进程2：鼓励 / 排解（OpenAI Agents 版）。

按后端传来的 style 选择不同指令的 Agent：
positive -> encourage（鼓励），negative -> comfort（排解），neutral -> neutral。
"""
from __future__ import annotations

from agents import Agent, Runner

from shared.config import get_settings
from shared.history import format_history
from shared.protocol import ReplyResult, SupportRequest
from shared.runtime import log
from shared.sentiment import fallback_reply

from .runtime_setup import configure_agents

_BASE_INSTRUCTIONS = """你是一位温暖、专业的中文心理陪伴者。
使用第二人称，直接对用户说话；150 字以内；不要使用 Markdown 标题或列表；
不要说教，不要提及"情感识别""进程""策略"等系统内部细节。"""

_STYLE_INSTRUCTIONS = {
    "encourage": "用户此刻情绪偏正向。请真诚地肯定他/她，具体指出值得高兴的地方，"
    "再给一个能延续这份状态的小建议。语气温暖、有活力。",
    "comfort": "用户此刻情绪偏负面。请先共情和接纳，不评判、不说教；"
    "再温和地帮其梳理压力，给 1-2 个可以立刻执行的小步骤。语气温柔、稳定。",
    "neutral": "用户情绪偏中性。请自然回应，表达在倾听，邀请对方补充更多信息。语气平和。",
}

_agents: dict[str, Agent] = {}


def _get_agent(style: str) -> Agent:
    agent = _agents.get(style)
    if agent is None:
        configure_agents()
        agent = Agent(
            name=f"supporter_{style}",
            instructions=(
                _BASE_INSTRUCTIONS
                + "\n\n回复策略："
                + _STYLE_INSTRUCTIONS.get(style, _STYLE_INSTRUCTIONS["neutral"])
            ),
            model=get_settings().model,
        )
        _agents[style] = agent
    return agent


def handle_support(request: SupportRequest) -> ReplyResult:
    settings = get_settings()

    if settings.llm_enabled:
        try:
            context = format_history(request.history)
            parts = []
            if context:
                parts.append(f"最近对话：\n{context}")
            parts.append(
                f"用户原话：{request.text}\n"
                f"情感识别：{request.sentiment}"
                f"（理由：{request.reason or '无'}）\n"
                f"请直接给出回复。"
            )
            prompt = "\n\n".join(parts)
            result = Runner.run_sync(_get_agent(request.style), prompt, max_turns=2)
            reply = str(result.final_output or "").strip()
            if reply:
                log(f"进程2 Agent 生成回复（style={request.style}）")
                return ReplyResult(
                    rid=request.rid,
                    reply=reply,
                    style=request.style,
                    sentiment=request.sentiment,
                    source="llm",
                )
        except Exception as exc:
            log(f"进程2 Agent 失败，退回模板回复：{exc}")

    return ReplyResult(
        rid=request.rid,
        reply=fallback_reply(request.style, request.text),
        style=request.style,
        sentiment=request.sentiment,
        source="rule",
    )
