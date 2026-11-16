"""全局配置：从环境变量或项目根目录的 .env 读取。"""
import os
from pathlib import Path

# 项目根目录（src/backend/app/config.py 上溯三级）
PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _load_dotenv(path: Path) -> None:
    """极简 .env 加载器，避免额外依赖；已存在的环境变量优先。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(PROJECT_ROOT / ".env")


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


# 数据目录
DATA_DIR = Path(_env("DATA_DIR", str(PROJECT_ROOT / "data")))
UPLOADS_DIR = DATA_DIR / "uploads"
PARSED_DIR = DATA_DIR / "parsed"
MILVUS_DB_FILE = str(DATA_DIR / "milvus.db")
DOCS_DB_FILE = DATA_DIR / "docs.db"

# Milvus
COLLECTION_NAME = _env("MILVUS_COLLECTION", "multimodal_rag")

# Kafka
KAFKA_BOOTSTRAP_SERVERS = _env("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
KAFKA_TOPIC = _env("KAFKA_TOPIC", "pdf-parse-requests")
KAFKA_GROUP_ID = _env("KAFKA_GROUP_ID", "parse-worker")

# 大模型（OpenAI 兼容接口，默认 DeepSeek）
LLM_API_KEY = _env("DEEPSEEK_API_KEY", "")
LLM_BASE_URL = _env("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
LLM_MODEL = _env("DEEPSEEK_MODEL", "deepseek-flash")

# 检索返回数量
TOP_K = int(_env("TOP_K", "4"))

# 前端静态目录
FRONTEND_DIR = PROJECT_ROOT / "src" / "frontend"


def make_data_dirs() -> None:
    """创建运行所需的数据目录。"""
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    PARSED_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_DB_FILE.parent.mkdir(parents=True, exist_ok=True)
