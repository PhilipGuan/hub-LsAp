"""运行时配置：从 .env 读取。"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    environment: str = "development"
    cors_origins: str = "http://localhost:5180"

    # 视觉感知 Provider: demo | openai | qwen
    vision_provider: str = "demo"

    # OpenAI 兼容多模态端点
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = "qwen3-vl-plus"

    # Qwen 原生 DashScope（预留）
    dashscope_api_key: str = ""
    dashscope_base_url: str = ""
    qwen_model: str = "qwen3-vl-plus"

    request_timeout_seconds: float = 60.0

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
