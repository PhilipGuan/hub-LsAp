"""编排单轮情绪识别与情感回应。"""

import logging
import re
from collections.abc import Sequence
from uuid import uuid4

from backend.agents.emotion_agent import EmotionAgentError, EmotionOutputError, detect_emotion
from backend.agents.response_agent import HistoryMessage, ResponseAgentError, generate_response
from backend.models.schemas import AgentStatus, ChatDebugInfo, ChatRequest, ChatResponse, EmotionResult


_LOGGER = logging.getLogger(__name__)
_GENERAL_REPLY = "我在听。如果你愿意，可以再说说发生了什么，或者你现在最想聊的是什么。"
_UNAVAILABLE_REPLY = "我暂时无法生成回复，请稍后再试。"

# 仅用于优先处理明确的自伤表达。措辞参考 NIMH 的安全与社会支持建议。
_SELF_HARM = re.compile(
    r"(?P<zh>(?:我\s*|(?:^|[，。！？；,!?])\s*)"
    r"(?:(?:现在|真的|很|最近|今晚|已经|刚刚|马上)\s*)*"
    r"(?:(?:想|要|准备|打算|计划|决定|在考虑|想过)\s*"
    r"(?:自杀|自伤|自残|轻生|伤害自己|结束(?:自己的)?生命|去死|死掉)"
    r"|不想(?:再|继续)?活(?:下去)?了?(?=$|[\s，。！？；,!?])"
    r"|活不下去了?|有(?:自杀|自伤|轻生)(?:的)?(?:想法|念头)|伤害了自己|自残了))"
    r"|(?P<en>\bi(?:\s+(?:want|plan|intend|am\s+going)|['’]m\s+going)\s+to\s+"
    r"(?:kill\s+myself|hurt\s+myself|end\s+my\s+life|die)\b"
    r"|\bi\s+(?:don['’]t|do\s+not)\s+want\s+to\s+live"
    r"(?:\s+any\s*more)?(?=\s*(?:[.!?,;]|$)))",
    re.IGNORECASE,
)
_SAFETY_REPLIES = {
    "zh": (
        "谢谢你愿意说出来。你现在安全吗？如果你可能马上伤害自己，或已经受伤，"
        "请立即联系当地急救服务。也可以现在联系一位你信任的人，请对方陪在你身边。"
    ),
    "en": (
        "I'm glad you told me. Are you safe right now? If you might hurt yourself soon or are already "
        "injured, contact local emergency services now. If you can, ask someone you trust to stay with you."
    ),
}


async def _detect_with_retry(message: str) -> EmotionResult:
    try:
        return await detect_emotion(message)
    except EmotionOutputError:
        return await detect_emotion(message)


async def _run_agents(
    message: str, history: Sequence[HistoryMessage]
) -> tuple[str, ChatDebugInfo]:
    try:
        emotion = await _detect_with_retry(message)
    except EmotionAgentError as error:
        _LOGGER.warning("情绪识别失败（%s）", type(error).__name__)
        reply = _GENERAL_REPLY if isinstance(error, EmotionOutputError) else _UNAVAILABLE_REPLY
        return reply, ChatDebugInfo(emotion_agent_status="failed", response_agent_status="skipped")

    response_status: AgentStatus = "success"
    try:
        reply = await generate_response(message, emotion, history)
    except ResponseAgentError as error:
        _LOGGER.warning("情感回应失败（%s）", type(error).__name__)
        reply = _UNAVAILABLE_REPLY
        response_status = "failed"

    return reply, ChatDebugInfo(
        emotion_agent_status="success", response_agent_status=response_status, emotion=emotion
    )


async def chat_service(
    request: ChatRequest, history: Sequence[HistoryMessage] = ()
) -> ChatResponse:
    """使用调用方传入的历史完成一轮对话，并返回会话 ID。"""
    risk = _SELF_HARM.search(request.message)
    if risk:
        reply = _SAFETY_REPLIES[risk.lastgroup or "zh"]
        debug = ChatDebugInfo(emotion_agent_status="skipped", response_agent_status="skipped")
    else:
        reply, debug = await _run_agents(request.message, history)

    return ChatResponse(
        session_id=request.session_id or str(uuid4()),
        reply=reply,
        debug=debug if request.debug else None,
    )
