"""全局配置：从项目根目录的 .env 读取。"""
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


# LLM（DeepSeek）
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
LLM_TIMEOUT = _float("LLM_TIMEOUT", 120)
LLM_MAX_RETRY = _int("LLM_MAX_RETRY", 3)

# 搜索（Bocha）
BOCHA_API_KEY = os.getenv("BOCHA_API_KEY", "")
BOCHA_SEARCH_URL = os.getenv("BOCHA_SEARCH_URL", "https://api.bocha.cn/v1/web-search")
SEARCH_COUNT = _int("SEARCH_COUNT", 8)
SEARCH_TIMEOUT = _float("SEARCH_TIMEOUT", 30)

# 研究编排
RESEARCH_MAX_ROUNDS = _int("RESEARCH_MAX_ROUNDS", 3)
MAX_FETCH_PER_ROUND = _int("MAX_FETCH_PER_ROUND", 6)
FETCH_TIMEOUT = _float("FETCH_TIMEOUT", 20)
MAX_FETCH_CHARS = _int("MAX_FETCH_CHARS", 6000)

DATA_DIR = ROOT / "data" / "research"
DATA_DIR.mkdir(parents=True, exist_ok=True)

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def setup_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(level=level, format=LOG_FORMAT)


if __name__ == "__main__":
    setup_logging()
    print("DEEPSEEK_API_KEY:", (DEEPSEEK_API_KEY[:8] + "...") if DEEPSEEK_API_KEY else "(未设置)")
    print("DEEPSEEK_BASE_URL:", DEEPSEEK_BASE_URL)
    print("DEEPSEEK_MODEL:", DEEPSEEK_MODEL)
    print("BOCHA_API_KEY:", (BOCHA_API_KEY[:8] + "...") if BOCHA_API_KEY else "(未设置)")
    print("RESEARCH_MAX_ROUNDS:", RESEARCH_MAX_ROUNDS)
    print("SEARCH_COUNT:", SEARCH_COUNT)
    print("DATA_DIR:", DATA_DIR)
