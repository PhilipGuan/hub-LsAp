from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "chinook.db"


@dataclass(frozen=True)
class Settings:
    database_url: str
    llm_model: str
    llm_base_url: str | None
    llm_api_key: str | None
    max_sql_repairs: int
    row_limit: int
    request_timeout: float


def _build_database_url() -> str:
    configured = os.getenv("DATABASE_URL", "").strip()
    if configured:
        return configured

    configured_path = os.getenv("SQLITE_DB_PATH", "").strip()
    db_path = Path(configured_path).expanduser().resolve() if configured_path else DEFAULT_DB_PATH
    return f"sqlite:///{db_path.as_posix()}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        database_url=_build_database_url(),
        llm_model=os.getenv("LLM_MODEL", "qwen-flash"),
        llm_base_url=os.getenv(
            "LLM_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ),
        llm_api_key=(
            os.getenv("LLM_API_KEY")
            or os.getenv("DASHSCOPE_API_KEY")
            or os.getenv("OPENAI_API_KEY")
        ),
        max_sql_repairs=int(os.getenv("SQL_AGENT_MAX_REPAIRS", "2")),
        row_limit=int(os.getenv("SQL_AGENT_ROW_LIMIT", "100")),
        request_timeout=float(os.getenv("LLM_TIMEOUT_SECONDS", "90")),
    )
