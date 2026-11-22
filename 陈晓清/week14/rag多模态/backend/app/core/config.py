# -*- coding: utf-8 -*-
"""后端集中配置：所有路径、密钥、开关都从这里读取。"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# backend/app/core/config.py -> backend/
BACKEND_DIR = Path(__file__).resolve().parents[2]
ROOT_DIR = BACKEND_DIR.parent

# 必须在导入 sentence_transformers / transformers 之前设置
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

load_dotenv(BACKEND_DIR / ".env")

DATA_DIR = Path(os.getenv("DATA_DIR", str(BACKEND_DIR / "data"))).resolve()
UPLOAD_DIR = DATA_DIR / "uploads"
PROCESSED_DIR = DATA_DIR / "processed"
CHAT_IMAGE_DIR = UPLOAD_DIR / "chat"
DB_PATH = DATA_DIR / "app.db"
MODELS_DIR = Path(os.getenv("MODELS_DIR", str(BACKEND_DIR / "models"))).resolve()

BGE_MODEL_PATH = str(MODELS_DIR / "bge-small-zh-v1.5")
CLIP_MODEL_PATH = str(MODELS_DIR / "jina-clip-v2")

# ---------------------------------------------------------------- 向量库
EMBED_DIM = int(os.getenv("EMBED_DIM", "512"))
MILVUS_URI = os.getenv(
    "MILVUS_URI",
    "https://in03-5cb3b56f3af9ebc.serverless.ali-cn-hangzhou.cloud.zilliz.com.cn",
)
MILVUS_TOKEN = os.getenv(
    "MILVUS_TOKEN",
    "9027d285f74e5ce113bf24162fc5cabe04b67db3ee25055f4748ea23785f00d0fa9b8217c108a04dc77c4a703b5860a7d39d7a7b",
)
MILVUS_COLLECTION = os.getenv("MILVUS_COLLECTION", "rag_data_new")

# ---------------------------------------------------------------- Kafka
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "localhost:9092")
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "rag-parse")
KAFKA_GROUP = os.getenv("KAFKA_GROUP", "rag-parse-worker")
KAFKA_ENABLED = os.getenv("KAFKA_ENABLED", "true").lower() in ("1", "true", "yes")

# ---------------------------------------------------------------- 大模型
LLM_API_KEY = os.getenv("OPENAI_API_KEY", "")
LLM_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
LLM_MODEL = os.getenv("OPENAI_MODEL", "qwen3.8-max")
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "120"))

# ---------------------------------------------------------------- MinerU
MINERU_BIN = os.getenv("MINERU_BIN", "mineru")
MINERU_TIER = os.getenv("MINERU_TIER", "flash")
MINERU_TIMEOUT = int(os.getenv("MINERU_TIMEOUT", "1800"))

# ---------------------------------------------------------------- 业务约束
TEXT_MAX_BYTES = int(os.getenv("TEXT_MAX_BYTES", "1000"))
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "300"))
TOP_K = int(os.getenv("TOP_K", "6"))
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "50"))
CHAT_INCLUDE_RETRIEVED_IMAGES = os.getenv("CHAT_INCLUDE_RETRIEVED_IMAGES", "true").lower() in (
    "1",
    "true",
    "yes",
)
MAX_INLINE_IMAGES = int(os.getenv("MAX_INLINE_IMAGES", "2"))

# ---------------------------------------------------------------- 状态
STATUS_PENDING = "pending"
STATUS_PARSING = "parsing"
STATUS_DONE = "done"
STATUS_FAILED = "failed"

STATUS_TEXT = {
    STATUS_PENDING: "待解析",
    STATUS_PARSING: "解析中",
    STATUS_DONE: "解析完成",
    STATUS_FAILED: "解析失败",
}


def ensure_dirs() -> None:
    for path in (DATA_DIR, UPLOAD_DIR, PROCESSED_DIR, CHAT_IMAGE_DIR):
        path.mkdir(parents=True, exist_ok=True)


def data_dir_prefix() -> str:
    """Milvus 过滤用的路径前缀，统一用正斜杠。"""
    return DATA_DIR.as_posix().rstrip("/") + "/"
