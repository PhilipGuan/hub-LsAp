"""配置：从项目根目录的 .env 读取"""
import os
from pathlib import Path

from dotenv import load_dotenv

# .env 放在项目根目录（backend 的上一级）
ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "qwen3.8-max").strip()

# 图片限制
MAX_IMAGE_MB = float(os.getenv("MAX_IMAGE_MB", "10"))
MAX_IMAGE_SIDE = int(os.getenv("MAX_IMAGE_SIDE", "1280"))

# 模型调用超时（秒）
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "60"))
