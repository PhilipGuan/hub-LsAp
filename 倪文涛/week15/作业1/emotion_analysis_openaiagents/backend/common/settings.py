import logging
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_file_encoding="utf-8", extra="ignore",
    )

    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_request_topic: str = "emotion-requests"
    kafka_classification_topic: str = "emotion-classifications"
    kafka_response_topic: str = "emotion-responses"
    kafka_classifier_group: str = "emotion-classifier-group"
    kafka_responder_group: str = "emotion-responder-group"
    kafka_api_group: str = "emotion-api-group"
    llm_model: str = ""
    llm_api_key: SecretStr = SecretStr("")
    llm_base_url: str = ""
    llm_timeout_seconds: float = Field(default=300, gt=0, le=300)
    backend_url: str = "http://127.0.0.1:8000"
    request_timeout_seconds: float = Field(default=300, gt=0)
    frontend_read_timeout_seconds: float = Field(default=300, gt=0)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @model_validator(mode="after")
    def validate_timeouts(self):
        if self.frontend_read_timeout_seconds < self.request_timeout_seconds:
            raise ValueError("前端读取超时不能小于后端等待超时")
        return self


def configure_logging(settings: Settings) -> None:
    logging.basicConfig(level=settings.log_level,
                        format="%(asctime)s %(levelname)s pid=%(process)d %(name)s %(message)s")
