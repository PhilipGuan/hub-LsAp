"""集中配置：路径和运行参数。"""
from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - requirements.txt 会安装
    load_dotenv = None


BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
DATA_DIR = BASE_DIR / "data" / "research"

if load_dotenv is not None:
    load_dotenv(ROOT_DIR / ".env")


HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8010"))

MODEL_NAME = os.getenv("MODEL_NAME", "deepseek-chat")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com/")

BOCHA_API_KEY = os.getenv("BOCHA_API_KEY", "")
BOCHA_SEARCH_COUNT = int(os.getenv("BOCHA_SEARCH_COUNT", "5"))

MAX_ROUNDS = int(os.getenv("MAX_ROUNDS", "2"))
USE_MOCK_SETTING = os.getenv("USE_MOCK", "auto").strip().lower()


def mock_mode() -> bool:
    """无 Key 或显式开启时使用 mock。"""
    if USE_MOCK_SETTING in {"1", "true", "yes", "on"}:
        return True
    if USE_MOCK_SETTING in {"0", "false", "no", "off"}:
        return not (OPENAI_API_KEY and BOCHA_API_KEY)
    return not (OPENAI_API_KEY and BOCHA_API_KEY)


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def today_str() -> str:
    from datetime import date

    return date.today().isoformat()
