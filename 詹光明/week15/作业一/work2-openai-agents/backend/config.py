"""后端统一配置。

所有配置都从环境变量（或项目根目录的 ``.env``）读取；
API key 只放 ``.env``，不写进代码，也不提交到仓库。

默认对接 DeepSeek 的 OpenAI 兼容接口。DeepSeek 不支持
``response_format=json_schema``，因此本项目使用 ``json_object`` 模式 +
提示词约束 + Pydantic 校验来获得结构化结果。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    """可选加载项目根目录 .env；没装 python-dotenv 时静默跳过。"""

    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover
        return
    load_dotenv(PROJECT_ROOT / ".env", override=False)


_load_dotenv()


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


# 出现这些表达时，即使模型没有识别出风险，也会切换到关怀模式。
DEFAULT_CRISIS_KEYWORDS = (
    "自杀",
    "自残",
    "自伤",
    "轻生",
    "想死",
    "不想活",
    "活不下去",
    "结束生命",
    "活着没意思",
    "想不开",
    "伤害自己",
    "跳楼",
    "割腕",
)


@dataclass(frozen=True)
class Settings:
    """运行配置。默认值对应 DeepSeek 的 OpenAI 兼容接口。"""

    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"

    # 情感识别需要稳定输出，温度设为 0；回复需要自然一些，温度高一点。
    emotion_temperature: float = 0.0
    reply_temperature: float = 0.8

    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 2
    agent_max_turns: int = 2

    # 是否给模型加 response_format=json_object（DeepSeek / OpenAI 都支持）。
    json_mode: bool = True

    # 拼进提示词的历史轮数（按消息条数计）。
    max_history_messages: int = 12
    max_message_chars: int = 4000

    backend_host: str = "127.0.0.1"
    backend_port: int = 8000

    crisis_keywords: tuple[str, ...] = DEFAULT_CRISIS_KEYWORDS

    @property
    def api_key_configured(self) -> bool:
        return bool(
            self.api_key
            and self.api_key.strip()
            and self.api_key not in {"sk-xxxxxx", "not-configured"}
        )

    @classmethod
    def from_env(cls) -> "Settings":
        raw_keywords = os.getenv("CRISIS_KEYWORDS", "").strip()
        keywords = (
            tuple(
                item.strip()
                for item in raw_keywords.replace("，", ",").split(",")
                if item.strip()
            )
            if raw_keywords
            else DEFAULT_CRISIS_KEYWORDS
        )

        return cls(
            api_key=os.getenv("OPENAI_API_KEY", ""),
            base_url=os.getenv("OPENAI_BASE_URL", cls.base_url),
            model=os.getenv("OPENAI_MODEL", cls.model),
            emotion_temperature=_env_float(
                "EMOTION_TEMPERATURE", cls.emotion_temperature
            ),
            reply_temperature=_env_float("REPLY_TEMPERATURE", cls.reply_temperature),
            llm_timeout_seconds=_env_float("LLM_TIMEOUT", cls.llm_timeout_seconds),
            llm_max_retries=_env_int("LLM_MAX_RETRIES", cls.llm_max_retries),
            agent_max_turns=_env_int("AGENT_MAX_TURNS", cls.agent_max_turns),
            json_mode=_env_bool("JSON_MODE", cls.json_mode),
            max_history_messages=_env_int(
                "MAX_HISTORY_MESSAGES", cls.max_history_messages
            ),
            max_message_chars=_env_int("MAX_MESSAGE_CHARS", cls.max_message_chars),
            backend_host=os.getenv("BACKEND_HOST", cls.backend_host),
            backend_port=_env_int("BACKEND_PORT", cls.backend_port),
            crisis_keywords=keywords,
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
