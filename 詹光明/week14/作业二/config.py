"""
项目统一配置。

所有配置都可以通过环境变量（或同目录下的 .env 文件）覆盖，
这样同一份代码既能在本地跑，也能在 AutoDL / 服务器上跑。
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# 可选：从 .env 读取配置（没装 python-dotenv 也不影响使用）
try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except Exception:  # pragma: no cover - 没装 dotenv 时走环境变量
    pass


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


def _path(name: str, default: str) -> Path:
    p = Path(os.getenv(name, default))
    return p if p.is_absolute() else BASE_DIR / p


# ---------------------------------------------------------------------------
# 本地目录
# ---------------------------------------------------------------------------
UPLOAD_DIR = _path("UPLOAD_DIR", "uploads")            # 原始 PDF
PROCESSED_DIR = _path("PROCESSED_DIR", "processed")    # MinerU 解析结果
CHAT_UPLOAD_DIR = _path("CHAT_UPLOAD_DIR", "chat_uploads")  # 对话中上传的图片
DB_PATH = _path("DB_PATH", "db.db")                    # sqlite 元信息

for _d in (UPLOAD_DIR, PROCESSED_DIR, CHAT_UPLOAD_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Kafka（上传端 -> 解析 worker）
# ---------------------------------------------------------------------------
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "rag-data")
KAFKA_GROUP_ID = os.getenv("KAFKA_GROUP_ID", "rag-parse-worker")

# ---------------------------------------------------------------------------
# Milvus 向量库
#   - 默认使用本地文件（Milvus Lite，Linux/macOS 可用）
#   - Windows / 远程集群请设置 MILVUS_URI + MILVUS_TOKEN
# ---------------------------------------------------------------------------
MILVUS_URI = os.getenv("MILVUS_URI", str(BASE_DIR / "milvus.db"))
MILVUS_TOKEN = os.getenv("MILVUS_TOKEN", "")
MILVUS_COLLECTION = os.getenv("MILVUS_COLLECTION", "rag_multimodal")

# ---------------------------------------------------------------------------
# 向量模型
# ---------------------------------------------------------------------------
HF_ENDPOINT = os.getenv("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_ENDPOINT", HF_ENDPOINT)

# 支持本地目录，也支持 HuggingFace 仓库名
BGE_MODEL = os.getenv("BGE_MODEL", "BAAI/bge-small-zh-v1.5")
CLIP_MODEL = os.getenv("CLIP_MODEL", "jinaai/jina-clip-v2")
CLIP_TRUNCATE_DIM = _int("CLIP_TRUNCATE_DIM", 1024)

BGE_DIM = _int("BGE_DIM", 512)          # bge-small-zh-v1.5 -> 512
CLIP_DIM = _int("CLIP_DIM", CLIP_TRUNCATE_DIM)
EMBEDDING_DEVICE = os.getenv("EMBEDDING_DEVICE", "")  # ""=自动，cpu / cuda

# ---------------------------------------------------------------------------
# PDF 解析
#   PARSER_BACKEND=mineru   使用 MinerU（推荐，质量最好）
#   PARSER_BACKEND=pymupdf  使用 PyMuPDF 兜底（本机没装 MinerU 时演示用）
#
#   MINERU_MODE 控制调用哪种 MinerU CLI：
#     auto  —— 自动识别（优先 mineru-kit，其次根据 --help 判断版本）
#     flat  —— MinerU 2.x/3.x：mineru -p in.pdf -o out -b pipeline
#     kit   —— MinerU 4.x：mineru-kit parse ... --format zip（含 images/）
#     parse —— MinerU 4.x：mineru parse ...（只输出 markdown，无本地图片）
# ---------------------------------------------------------------------------
PARSER_BACKEND = os.getenv("PARSER_BACKEND", "mineru").lower()
PARSER_MODE = os.getenv("MINERU_MODE", "auto").lower()
MINERU_CMD = os.getenv("MINERU_CMD", "mineru")
MINERU_KIT_CMD = os.getenv("MINERU_KIT_CMD", "mineru-kit")
MINERU_BACKEND = os.getenv("MINERU_BACKEND", "pipeline")  # flat 模式：pipeline / vlm-http-client ...
MINERU_TIER = os.getenv("MINERU_TIER", "standard")        # kit 模式：flash/basic/standard/advanced
MINERU_API_URL = os.getenv("MINERU_API_URL", "http://127.0.0.1:30000")
MINERU_EXTRA_ARGS = os.getenv("MINERU_EXTRA_ARGS", "")
PARSE_TIMEOUT = _int("PARSE_TIMEOUT", 1200)
CHUNK_SIZE = _int("CHUNK_SIZE", 256)

# ---------------------------------------------------------------------------
# 检索
# ---------------------------------------------------------------------------
TOP_K = _int("TOP_K", 5)
MAX_CONTEXT_CHARS = _int("MAX_CONTEXT_CHARS", 8000)
MAX_CONTEXT_IMAGES = _int("MAX_CONTEXT_IMAGES", 3)
MAX_USER_IMAGES = _int("MAX_USER_IMAGES", 3)

# ---------------------------------------------------------------------------
# 多模态大模型（OpenAI 兼容接口，默认 DashScope / Qwen-VL）
# ---------------------------------------------------------------------------
LLM_BASE_URL = os.getenv(
    "LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
)
LLM_API_KEY = os.getenv("LLM_API_KEY") or os.getenv("DASHSCOPE_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen-vl-max-latest")
LLM_TEMPERATURE = _float("LLM_TEMPERATURE", 0.2)
