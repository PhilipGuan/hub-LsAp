from agents import OpenAIChatCompletionsModel
from openai import AsyncOpenAI

from backend.common.settings import Settings


def create_model(settings: Settings) -> OpenAIChatCompletionsModel:
    if not settings.llm_model.strip() or not settings.llm_api_key.get_secret_value().strip():
        raise ValueError("请在 .env 中填写 LLM_MODEL 和 LLM_API_KEY")
    return OpenAIChatCompletionsModel(
        model=settings.llm_model,
        openai_client=AsyncOpenAI(
            api_key=settings.llm_api_key.get_secret_value(),
            base_url=settings.llm_base_url or None,
            timeout=settings.llm_timeout_seconds,
            max_retries=2,
        ),
    )
