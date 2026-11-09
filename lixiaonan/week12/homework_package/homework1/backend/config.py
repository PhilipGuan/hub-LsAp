"""全局配置：从项目根目录的 .env 读取，未配置时使用默认值。"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# 智谱开放平台 OpenAI 兼容接口
API_KEY = os.getenv("API_KEY", "").strip()
BASE_URL = os.getenv("BASE_URL", "https://open.bigmodel.cn/api/paas/v4").rstrip("/")
MODEL = os.getenv("MODEL", "glm-4.1v-flash")

# 图片约束
MAX_IMAGE_BYTES = int(os.getenv("MAX_IMAGE_BYTES", str(5 * 1024 * 1024)))  # 5MB
MAX_IMAGE_SIDE = int(os.getenv("MAX_IMAGE_SIDE", "2048"))  # 后端兜底压缩的最长边

# 模型请求超时（秒）
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "60"))
