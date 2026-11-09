"""运行配置。API key 只从环境变量或项目根目录的 .env 读取。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "chinook.db"


def _load_dotenv() -> None:
    """可选加载 作业二/.env；未安装 python-dotenv 时静默跳过。"""

    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(PROJECT_ROOT / ".env", override=False)


_load_dotenv()


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
    db_path: Path = DEFAULT_DB_PATH
    primary_model: str = "deepseek-chat"
    summary_model: str = "deepseek-chat"
    base_url: str = "https://api.deepseek.com"
    api_key: str = ""
    # LangChain 结构化输出的实现方式：
    # function_calling（默认，工具调用）/ json_schema（响应格式）/ json_mode。
    structured_output_method: str = "function_calling"
    max_repair_attempts: int = 3
    max_result_rows: int = 200
    max_summary_rows: int = 50
    max_cell_chars: int = 2000
    query_timeout_seconds: float = 5.0
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 2

    @classmethod
    def from_env(cls) -> "Settings":
        db_path = Path(os.getenv("SQL_AGENT_DB") or str(cls.db_path))
        if not db_path.is_absolute():
            db_path = PROJECT_ROOT / db_path

        return cls(
            db_path=db_path,
            primary_model=os.getenv("SQL_AGENT_MODEL", cls.primary_model),
            summary_model=os.getenv("SQL_AGENT_SUMMARY_MODEL", cls.summary_model),
            base_url=os.getenv("OPENAI_BASE_URL", cls.base_url),
            api_key=(
                os.getenv("OPENAI_API_KEY", "")
                or os.getenv("SQL_AGENT_API_KEY", "")
                or cls.api_key
            ),
            structured_output_method=os.getenv(
                "SQL_AGENT_STRUCTURED_METHOD", cls.structured_output_method
            ),
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
            llm_max_retries=_env_int("SQL_AGENT_LLM_RETRIES", cls.llm_max_retries),
        )


settings = Settings.from_env()
