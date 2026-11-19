"""集中加载 .env，并提供 ChatOpenAI 客户端工厂。

所有 LLM 调用（测试脚本、后续 LangGraph worker）都复用这里的配置，
避免重复读取环境变量或在不同模块里硬编码模型参数。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    api_key: str
    base_url: str
    model: str
    temperature: float
    max_tokens: int


def load_settings() -> Settings:
    """读取并校验 LLM 相关环境变量，缺失时给出明确报错。"""
    values = {
        "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY", "").strip(),
        "OPENAI_BASE_URL": os.getenv("OPENAI_BASE_URL", "").strip(),
        # 兼容 README 里的 QWEN_MODEL 写法
        "OPENAI_MODEL": (os.getenv("OPENAI_MODEL") or os.getenv("QWEN_MODEL") or "").strip(),
    }
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise RuntimeError(
            f"缺少环境变量: {', '.join(missing)}。请参考 .env.example 配置 {PROJECT_ROOT / '.env'}"
        )

    return Settings(
        api_key=values["OPENAI_API_KEY"],
        base_url=values["OPENAI_BASE_URL"],
        model=values["OPENAI_MODEL"],
        temperature=float(os.getenv("LLM_TEMPERATURE", "0.7")),
        max_tokens=int(os.getenv("LLM_MAX_TOKENS", "512")),
    )


@dataclass(frozen=True)
class KafkaSettings:
    bootstrap_servers: str
    raw_input_topic: str
    sentiment_result_topic: str
    sentiment_worker_group_id: str
    reply_worker_group_id: str
    backend_group_id: str
    request_timeout_seconds: float


def load_kafka_settings() -> KafkaSettings:
    """Kafka 配置全部有 README 约定的默认值，未写进 .env 也能直接运行。"""
    return KafkaSettings(
        bootstrap_servers=os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
        raw_input_topic=os.getenv("RAW_INPUT_TOPIC", "raw_input"),
        sentiment_result_topic=os.getenv("SENTIMENT_RESULT_TOPIC", "sentiment_result"),
        sentiment_worker_group_id=os.getenv("SENTIMENT_WORKER_GROUP_ID", "sentiment-worker"),
        reply_worker_group_id=os.getenv("REPLY_WORKER_GROUP_ID", "reply-worker"),
        backend_group_id=os.getenv("BACKEND_GROUP_ID", "backend-api"),
        request_timeout_seconds=float(os.getenv("REQUEST_TIMEOUT_SECONDS", "60")),
    )


def get_llm(**overrides: object) -> ChatOpenAI:
    """构造 ChatOpenAI，可传入 temperature 等参数覆盖 .env 默认值。"""
    settings = load_settings()
    params: dict[str, object] = {
        "model": settings.model,
        "api_key": settings.api_key,
        "base_url": settings.base_url,
        "temperature": settings.temperature,
        "max_tokens": settings.max_tokens,
    }
    params.update(overrides)
    return ChatOpenAI(**params)
