"""集中读取 LLM 配置。

配置全部来自环境变量（可选 .env）。子进程由 multiprocessing 启动时会继承环境变量，
所以四个进程看到的是同一份配置。

不配置任何 API Key 也能运行：进程1 / 进程2 会自动退回本地词典 + 模板模式。
"""
from __future__ import annotations

import os
from dataclasses import dataclass

try:  # python-dotenv 是可选项
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    def load_dotenv(*args, **kwargs):  # type: ignore[misc]
        return False

load_dotenv()

_DEFAULT_MODEL = "gpt-4o-mini"


def _first_env(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return None


@dataclass(frozen=True)
class Settings:
    api_key: str | None
    base_url: str | None
    model: str
    timeout: float

    @property
    def llm_enabled(self) -> bool:
        return bool(self.api_key)


def _load() -> Settings:
    deepseek_key = _first_env("DEEPSEEK_API_KEY")
    api_key = _first_env(
        "OPENAI_API_KEY",
        "DEEPSEEK_API_KEY",
        "DASHSCOPE_API_KEY",
        "MOONSHOT_API_KEY",
    )
    base_url = _first_env("OPENAI_BASE_URL", "LLM_BASE_URL")
    model = _first_env("OPENAI_MODEL", "LLM_MODEL")

    # 只设置了 DEEPSEEK_API_KEY 时，自动补上 DeepSeek 的默认地址与模型
    if base_url is None and deepseek_key and api_key == deepseek_key:
        base_url = "https://api.deepseek.com/v1"
        model = model or "deepseek-chat"

    try:
        timeout = float(_first_env("LLM_TIMEOUT") or "60")
    except ValueError:
        timeout = 60.0

    return Settings(
        api_key=api_key,
        base_url=base_url,
        model=model or _DEFAULT_MODEL,
        timeout=timeout,
    )


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = _load()
    return _settings
