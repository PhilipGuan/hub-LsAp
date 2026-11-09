"""运行配置。API key 只从环境变量或 `src/sql_agent/.env` 读取。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = Path(__file__).with_name(".env")

# 已存在的环境变量优先；.env 只做兜底，文件不存在时静默跳过。
load_dotenv(ENV_FILE, override=False)


def _env_str(name: str, default: str | None) -> str | None:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    db_path: Path = PROJECT_ROOT / "ref" / "chinook.db"
    primary_model: str = "qwen-flash"
    summary_model: str = "qwen-flash"
    base_url: str = "https://ws-xe8mb1le5ylpj9zn.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
    max_repair_attempts: int = 3
    max_result_rows: int = 200
    max_summary_rows: int = 50
    max_cell_chars: int = 2000
    query_timeout_seconds: float = 5.0
    llm_timeout_seconds: float = 60.0

    @classmethod
    def from_env(cls) -> "Settings":
        db_path = Path(_env_str("SQL_AGENT_DB", str(cls.db_path)) or cls.db_path)
        if not db_path.is_absolute():
            db_path = PROJECT_ROOT / db_path

        # .env 的 OPENAI_MODEL 同时作为主模型和汇总模型的默认值。
        shared_model = _env_str("OPENAI_MODEL", None)

        return cls(
            db_path=db_path,
            primary_model=_env_str(
                "SQL_AGENT_MODEL", shared_model or cls.primary_model
            ),
            summary_model=_env_str(
                "SQL_AGENT_SUMMARY_MODEL", shared_model or cls.summary_model
            ),
            base_url=_env_str("OPENAI_BASE_URL", cls.base_url),
            max_repair_attempts=_env_int(
                "SQL_AGENT_MAX_REPAIR_ATTEMPTS", cls.max_repair_attempts
            ),
            max_result_rows=_env_int("SQL_AGENT_MAX_ROWS", cls.max_result_rows),
            max_summary_rows=_env_int(
                "SQL_AGENT_MAX_SUMMARY_ROWS", cls.max_summary_rows
            ),
            max_cell_chars=_env_int("SQL_AGENT_MAX_CELL_CHARS", cls.max_cell_chars),
            query_timeout_seconds=_env_float(
                "SQL_AGENT_QUERY_TIMEOUT", cls.query_timeout_seconds
            ),
            llm_timeout_seconds=_env_float(
                "SQL_AGENT_LLM_TIMEOUT", cls.llm_timeout_seconds
            ),
        )


settings = Settings.from_env()
