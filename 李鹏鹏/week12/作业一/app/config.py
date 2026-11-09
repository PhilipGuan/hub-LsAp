"""Runtime configuration for the corridor fire-safety inspector.

Everything is driven by environment variables (optionally loaded from `.env`),
so API keys never need to live in source code or be shipped to the browser.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")


def _env(*names: str, default: str = "") -> str:
    """Return the first non-empty environment variable among *names*."""
    for name in names:
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    return default


def _int_env(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class ProviderConfig:
    id: str
    label: str
    model: str
    base_url: str
    api_key: str
    description: str

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)


QWEN = ProviderConfig(
    id="qwen",
    label="Qwen VL",
    model=_env("QWEN_MODEL", default="qwen-vl-max-latest"),
    base_url=_env(
        "QWEN_BASE_URL",
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
    ),
    api_key=_env("QWEN_API_KEY", "DASHSCOPE_API_KEY"),
    description="通义千问视觉大模型 · 阿里云百炼",
)

DEEPSEEK = ProviderConfig(
    id="deepseek",
    label="DeepSeek Flash 4.1",
    model=_env("DEEPSEEK_MODEL", default="deepseek-v4.1-flash"),
    base_url=_env("DEEPSEEK_BASE_URL", default="https://api.deepseek.com"),
    api_key=_env("DEEPSEEK_API_KEY", "DS_API_KEY"),
    description="DeepSeek 多模态 Flash 模型",
)

PROVIDERS: dict[str, ProviderConfig] = {p.id: p for p in (QWEN, DEEPSEEK)}

_requested_default = _env("DEFAULT_PROVIDER", default="qwen").lower()
DEFAULT_PROVIDER = (
    _requested_default if _requested_default in PROVIDERS else "qwen"
)

HOST = _env("HOST", default="0.0.0.0")
PORT = _int_env("PORT", 8000)
MAX_UPLOAD_MB = _int_env("MAX_UPLOAD_MB", 12)
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
MAX_IMAGE_SIDE = _int_env("MAX_IMAGE_SIDE", 1600, minimum=512)
REQUEST_TIMEOUT = _int_env("REQUEST_TIMEOUT", 90, minimum=10)
OPEN_BROWSER = _env("OPEN_BROWSER", default="1") not in {"0", "false", "no"}


def get_provider(provider_id: str | None) -> ProviderConfig:
    """Look up a provider, falling back to the configured default."""
    key = (provider_id or DEFAULT_PROVIDER or "qwen").strip().lower()
    provider = PROVIDERS.get(key)
    if provider is None:
        raise KeyError(key)
    return provider


def public_providers() -> list[dict]:
    """Provider metadata that is safe to expose to the browser."""
    default_id = DEFAULT_PROVIDER if DEFAULT_PROVIDER in PROVIDERS else "qwen"
    return [
        {
            "id": p.id,
            "label": p.label,
            "model": p.model,
            "description": p.description,
            "configured": p.configured,
            "default": p.id == default_id,
        }
        for p in PROVIDERS.values()
    ]
