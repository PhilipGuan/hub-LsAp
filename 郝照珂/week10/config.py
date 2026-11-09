"""两个作业共用的模型配置，密钥只从环境变量或本目录 .env 读取。"""
import os
from pathlib import Path

from agents import OpenAIChatCompletionsModel, set_tracing_disabled
from dotenv import load_dotenv
from openai import AsyncOpenAI


def make_model() -> OpenAIChatCompletionsModel:
    load_dotenv(Path(__file__).with_name('.env'))
    key = os.getenv('OPENAI_API_KEY') or os.getenv('DEEPSEEK_API_KEY')
    if not key or key == 'your-api-key':
        raise ValueError('请在 .env 或环境变量中设置 OPENAI_API_KEY。')
    set_tracing_disabled(True)
    return OpenAIChatCompletionsModel(
        model=os.getenv('OPENAI_MODEL', 'deepseek-chat'),
        openai_client=AsyncOpenAI(
            api_key=key,
            base_url=os.getenv('OPENAI_BASE_URL', 'https://api.deepseek.com'),
            timeout=90.0,
            max_retries=1,
        ),
    )
