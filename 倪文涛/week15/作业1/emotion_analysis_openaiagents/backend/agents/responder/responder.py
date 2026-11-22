import logging
import time

from agents import Agent, ModelSettings, RunConfig, Runner

from backend.model.schemas import SuccessResult

logger = logging.getLogger(__name__)

PROMPTS = {
    "positive": "你是正向鼓励助手。结合用户的具体喜悦或进展给予自然、适度的肯定，避免空泛夸奖。",
    "negative": "你是负面情绪疏导助手。先简短理解用户感受，再给出一个温和、可执行的建议，避免说教和轻率保证。",
    "neutral": "你是中性问题回复助手。直接回答当前问题的核心；对普通陈述自然回应。不知道时如实说明。",
}

REPLY_STYLE = " 使用中文，全文约50字，尽量控制在40至60字、1至2句话。只给出最终回复，不输出分析过程、标题或列表。"


class Responder:
    def __init__(self, model):
        self.agents = {
            emotion: Agent(
                name=f"{emotion}_agent",
                instructions=prompt + REPLY_STYLE,
                model=model, model_settings=ModelSettings(
                    temperature=0, extra_body={"enable_thinking": False},
                ),
            )
            for emotion, prompt in PROMPTS.items()
        }

    async def __call__(self, message):
        started = time.monotonic()
        agent = self.agents[message.emotion]
        logger.info("request_id=%s 开始生成回复 emotion=%s agent=%s", message.request_id, message.emotion, agent.name)
        result = await Runner.run(
            agent, message.user_input, max_turns=1,
            run_config=RunConfig(tracing_disabled=True),
        )
        reply = result.final_output
        if not isinstance(reply, str) or not reply.strip():
            raise ValueError("模型未生成完整文本回复")
        logger.info("request_id=%s 回复生成完成 reply_chars=%s elapsed=%.3fs",
                    message.request_id, len(reply), time.monotonic() - started)
        return SuccessResult(request_id=message.request_id, emotion=message.emotion,
                             agent=agent.name, reply=reply)
