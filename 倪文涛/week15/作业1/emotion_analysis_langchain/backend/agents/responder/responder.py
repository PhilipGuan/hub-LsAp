import logging
import time

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from backend.model.schemas import SuccessResult

logger = logging.getLogger(__name__)

PROMPTS = {
    "positive": "你是正向鼓励助手。针对用户当前的喜悦或进展，给予具体、适度的鼓励。",
    "negative": "你是负面情绪疏导助手。先理解用户感受，再提供温和、可执行的建议，避免说教和轻率保证。",
    "neutral": "你是中性问题回复助手。对当前问题简短直接地回答；对普通陈述自然回应。不知道时如实说明。",
}


class Responder:
    def __init__(self, model):
        self.model = model

    async def __call__(self, message):
        started = time.monotonic()
        agent = f"{message.emotion}_agent"
        logger.info("request_id=%s 开始生成回复 emotion=%s agent=%s", message.request_id, message.emotion, agent)
        response = await self.model.ainvoke([
            SystemMessage(content=PROMPTS[message.emotion] + " 使用中文，通常回复 2 至 4 句话。"),
            HumanMessage(content=message.user_input),
        ])
        if not isinstance(response, AIMessage) or response.tool_calls or not response.text.strip():
            raise ValueError("模型未生成完整文本回复")
        logger.info("request_id=%s 回复生成完成 reply_chars=%s elapsed=%.3fs",
                    message.request_id, len(response.text), time.monotonic() - started)
        return SuccessResult(request_id=message.request_id, emotion=message.emotion,
                             agent=agent, reply=response.text)
