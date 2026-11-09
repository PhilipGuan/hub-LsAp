"""只在启动时读取和检查配置。"""

import math
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
MAX_IMAGE_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True)
class Settings:
    api_key: str
    base_url: str
    model: str
    timeout: float


def load_settings() -> Settings:
    load_dotenv(ROOT / ".env")
    api_key = os.getenv("SILICONFLOW_API_KEY", "").strip()
    model = os.getenv("SILICONFLOW_MODEL", "").strip()
    base_url = os.getenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1").strip()
    if not api_key or not model:
        raise RuntimeError("请在 .env 中填写 SILICONFLOW_API_KEY 和 SILICONFLOW_MODEL")
    url = urlsplit(base_url)
    if url.scheme != "https" or not url.netloc or url.username or url.password:
        raise RuntimeError("SILICONFLOW_BASE_URL 必须是有效的 HTTPS 服务地址")
    try:
        timeout = float(os.getenv("MODEL_TIMEOUT_SECONDS", "300"))
        if not math.isfinite(timeout) or not 0 < timeout <= 300:
            raise ValueError
    except ValueError:
        raise RuntimeError("MODEL_TIMEOUT_SECONDS 必须大于 0 且不超过 300 秒") from None
    return Settings(api_key, base_url, model, timeout)
