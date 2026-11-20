"""Application configuration, loaded from environment variables."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Tiny .env loader so the demo has no extra runtime dependency."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv(BASE_DIR / ".env")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


# --- Storage -----------------------------------------------------------------
DATA_DIR = Path(os.getenv("MMRAG_DATA_DIR", str(BASE_DIR / "data"))).resolve()
UPLOAD_DIR = DATA_DIR / "uploads"
PARSED_DIR = DATA_DIR / "parsed"
LOG_DIR = DATA_DIR / "logs"
DB_PATH = Path(os.getenv("MMRAG_DB_PATH", str(DATA_DIR / "mmrag.db"))).resolve()

# --- Kafka -------------------------------------------------------------------
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
INGEST_TOPIC = os.getenv("MMRAG_INGEST_TOPIC", "mmrag.ingest")
STATUS_TOPIC = os.getenv("MMRAG_STATUS_TOPIC", "mmrag.status")
WORKER_GROUP = os.getenv("MMRAG_WORKER_GROUP", "mmrag-ingest-workers")
API_EVENT_GROUP_PREFIX = os.getenv("MMRAG_API_EVENT_GROUP", "mmrag-api-events")

# --- MinerU ------------------------------------------------------------------
MINERU_BIN = os.getenv("MINERU_BIN") or shutil.which("mineru-kit") or "mineru-kit"
MINERU_TIER = os.getenv("MINERU_TIER", "basic")
# Basic is the reliable local fallback while the shared GPU is busy with vLLM.
MINERU_FALLBACK_TIER = os.getenv("MINERU_FALLBACK_TIER", "basic")
MINERU_TIMEOUT_SECONDS = _env_int("MINERU_TIMEOUT_SECONDS", 1800)

# --- OpenAI-compatible multimodal LLM (local vLLM / Qwen) --------------------
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://localhost:8000/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "EMPTY")
LLM_MODEL = os.getenv("LLM_MODEL", "models/Qwen3.5-0.8B/")
LLM_VISION_MODEL = os.getenv("LLM_VISION_MODEL", LLM_MODEL)
LLM_TIMEOUT_SECONDS = _env_int("LLM_TIMEOUT_SECONDS", 180)

# --- Ingestion / retrieval ---------------------------------------------------
CAPTION_IMAGES = _env_bool("MMRAG_CAPTION_IMAGES", True)
CAPTION_CONCURRENCY = _env_int("MMRAG_CAPTION_CONCURRENCY", 2)
CAPTION_MAX_EDGE = _env_int("MMRAG_CAPTION_MAX_EDGE", 1280)
CAPTION_TIMEOUT_SECONDS = _env_int("MMRAG_CAPTION_TIMEOUT_SECONDS", 60)
QUERY_IMAGE_TIMEOUT_SECONDS = _env_int("MMRAG_QUERY_IMAGE_TIMEOUT_SECONDS", 45)
DOCUMENT_MAX_MB = _env_int("MMRAG_DOCUMENT_MAX_MB", 100)
CHAT_IMAGE_MAX_MB = _env_int("MMRAG_CHAT_IMAGE_MAX_MB", 10)
QUERY_IMAGE_AUGMENT = _env_bool("MMRAG_QUERY_IMAGE_AUGMENT", True)
RETRIEVAL_TEXT_TOP_K = _env_int("MMRAG_RETRIEVAL_TEXT_TOP_K", 6)
RETRIEVAL_IMAGE_TOP_K = _env_int("MMRAG_RETRIEVAL_IMAGE_TOP_K", 4)
RETRIEVAL_CANDIDATES = _env_int("MMRAG_RETRIEVAL_CANDIDATES", 40)
MAX_CONTEXT_CHARS = _env_int("MMRAG_MAX_CONTEXT_CHARS", 9000)
MAX_VISION_CONTEXTS = _env_int("MMRAG_MAX_VISION_CONTEXTS", 3)
MAX_LLM_HISTORY_TURNS = _env_int("MMRAG_MAX_HISTORY_TURNS", 6)
LLM_MAX_TOKENS = _env_int("MMRAG_LLM_MAX_TOKENS", 1400)

# --- Web ---------------------------------------------------------------------
APP_HOST = os.getenv("MMRAG_HOST", "0.0.0.0")
APP_PORT = _env_int("MMRAG_PORT", 8080)
APP_TITLE = "MinerU × Kafka 多模态 RAG"


for directory in (DATA_DIR, UPLOAD_DIR, PARSED_DIR, LOG_DIR):
    directory.mkdir(parents=True, exist_ok=True)
