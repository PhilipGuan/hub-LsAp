from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_DIR / ".env")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    api_key: str
    base_url: str
    model: str
    db_path: Path
    max_rewrites: int
    max_rows: int
    query_timeout_s: int
    cache_dir: Path

    deadline_s: float = 20.0
    max_llm_calls: int = 8
    max_total_tokens: int = 60_000
    max_cost_cny: float = 0.10
    llm_timeout_cap_s: float = 15.0
    min_budget_s: float = 2.0
    price_input_cny_per_m: float = 2.0
    price_output_cny_per_m: float = 8.0

    semantic_dir: Path = PROJECT_DIR / "semantic"
    trace_dir: Path = PROJECT_DIR / "traces"
    feedback_db: Path = PROJECT_DIR / "feedback.db"
    retention_days: int = 30
    intent_llm_fallback: bool = False

    max_sql_chars: int = 20_000
    max_join_tables: int = 8
    app_version: str = "mvp-1"
    prompt_version: str = "prompt-v2"
    eval_runs: int = 3
    regression_drop: int = 2


def load_settings() -> Settings:
    raw_db = os.getenv("CHINOOK_DB", "data/chinook.db")
    db_path = Path(raw_db)
    if not db_path.is_absolute():
        db_path = (PROJECT_DIR / db_path).resolve()

    return Settings(
        api_key=os.getenv("DEEPSEEK_API_KEY", "").strip(),
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip(),
        model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip(),
        db_path=db_path,
        max_rewrites=_env_int("SQL_AGENT_MAX_REWRITE", 3),
        max_rows=_env_int("SQL_AGENT_MAX_ROWS", 100),
        query_timeout_s=_env_int("SQL_AGENT_QUERY_TIMEOUT", 10),
        cache_dir=PROJECT_DIR / ".cache",
        deadline_s=_env_float("SQL_AGENT_DEADLINE", 20.0),
        max_llm_calls=_env_int("SQL_AGENT_MAX_LLM_CALLS", 8),
        max_total_tokens=_env_int("SQL_AGENT_MAX_TOKENS", 60_000),
        max_cost_cny=_env_float("SQL_AGENT_MAX_COST", 0.10),
        llm_timeout_cap_s=_env_float("SQL_AGENT_LLM_TIMEOUT_CAP", 15.0),
        min_budget_s=_env_float("SQL_AGENT_MIN_BUDGET", 2.0),
        price_input_cny_per_m=_env_float("DEEPSEEK_PRICE_INPUT", 2.0),
        price_output_cny_per_m=_env_float("DEEPSEEK_PRICE_OUTPUT", 8.0),
        semantic_dir=PROJECT_DIR / "semantic",
        trace_dir=PROJECT_DIR / "traces",
        feedback_db=PROJECT_DIR / "feedback.db",
        retention_days=_env_int("SQL_AGENT_RETENTION_DAYS", 30),
        intent_llm_fallback=_env_bool("SQL_AGENT_INTENT_LLM", False),
        max_sql_chars=_env_int("SQL_AGENT_MAX_SQL_CHARS", 20_000),
        max_join_tables=_env_int("SQL_AGENT_MAX_JOINS", 8),
        eval_runs=_env_int("SQL_AGENT_EVAL_RUNS", 3),
    )


settings = load_settings()
