"""集中配置：环境变量来自项目根目录的 .env，相对路径按项目根解析。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _env(name: str, default: str) -> str:
    return os.getenv(name, default)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    kafka_bootstrap: str = field(default_factory=lambda: _env("KAFKA_BOOTSTRAP", "localhost:9092"))

    qwen_api_key: str = field(default_factory=lambda: _env("QWEN_API_KEY", ""))
    qwen_base_url: str = field(
        default_factory=lambda: _env(
            "QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
    )
    qwen_model: str = field(default_factory=lambda: _env("QWEN_MODEL", "qwen3.8-flash"))

    emotion_timeout: float = field(default_factory=lambda: _env_float("EMOTION_TIMEOUT", 15.0))
    reply_timeout: float = field(default_factory=lambda: _env_float("REPLY_TIMEOUT", 30.0))

    sqlite_path: str = field(
        default_factory=lambda: _env("SQLITE_PATH", "data/checkpoints.sqlite")
    )
    history_limit: int = field(default_factory=lambda: _env_int("HISTORY_LIMIT", 10))

    topic_emotion_request: str = field(
        default_factory=lambda: _env("TOPIC_EMOTION_REQUEST", "emotion-request")
    )
    topic_emotion_result: str = field(
        default_factory=lambda: _env("TOPIC_EMOTION_RESULT", "emotion-result")
    )
    topic_reply_request: str = field(
        default_factory=lambda: _env("TOPIC_REPLY_REQUEST", "reply-request")
    )
    topic_reply_token: str = field(
        default_factory=lambda: _env("TOPIC_REPLY_TOKEN", "reply-token")
    )

    backend_emotion_group: str = field(
        default_factory=lambda: _env("BACKEND_EMOTION_GROUP", "backend-emotion-group")
    )
    backend_reply_group: str = field(
        default_factory=lambda: _env("BACKEND_REPLY_GROUP", "backend-reply-group")
    )
    emotion_worker_group: str = field(
        default_factory=lambda: _env("EMOTION_WORKER_GROUP", "emotion-worker")
    )
    reply_worker_group: str = field(
        default_factory=lambda: _env("REPLY_WORKER_GROUP", "reply-worker")
    )

    @property
    def sqlite_file(self) -> Path:
        path = Path(self.sqlite_path)
        return path if path.is_absolute() else PROJECT_ROOT / path

    @property
    def llm_ready(self) -> bool:
        return bool(self.qwen_api_key) and not self.qwen_api_key.startswith("sk-xxxx")

    def ensure_sqlite_dir(self) -> None:
        self.sqlite_file.parent.mkdir(parents=True, exist_ok=True)


settings = Settings()
