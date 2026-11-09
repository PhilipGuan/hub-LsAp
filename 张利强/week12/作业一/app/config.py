"""应用配置：统一从环境变量 / .env 读取。"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
# 以项目内 .env 为准：避免 shell 里遗留的旧变量（例如失效的 DASHSCOPE_API_KEY）覆盖新配置
load_dotenv(BASE_DIR / ".env", override=True)


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


class Settings:
    """运行时配置。所有字段都可以通过 .env 覆盖。"""

    base_url: str = _env("VISION_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")
    api_key: str = _env("DASHSCOPE_API_KEY") or _env("VISION_API_KEY")
    model: str = _env("VISION_MODEL", "deepseek-v4.1-flash")

    # 没有可用 Key 时可置 1，用规则假数据把前后端流程跑通
    mock_mode: bool = _env("MOCK_MODE", "0").lower() in {"1", "true", "yes", "on"}

    max_image_mb: int = int(_env("MAX_IMAGE_MB", "10"))
    request_timeout: float = float(_env("REQUEST_TIMEOUT", "90"))
    image_max_side: int = int(_env("IMAGE_MAX_SIDE", "1280"))
    jpeg_quality: int = int(_env("JPEG_QUALITY", "85"))
    # 推理型模型会先消耗 token 思考，给足输出空间，否则 content 可能为空
    max_output_tokens: int = int(_env("MAX_OUTPUT_TOKENS", "3000"))

    @property
    def max_image_bytes(self) -> int:
        return self.max_image_mb * 1024 * 1024

    @property
    def key_configured(self) -> bool:
        return bool(self.api_key)


settings = Settings()
