"""集中配置：API Key / 模型 / 端点。

优先级：环境变量 > backend/.env 文件 > 默认值。
两个供应商（DeepSeek / Qwen）都走 OpenAI 兼容的 chat/completions 接口，
因此后端可以用同一套代码调用。
"""
from __future__ import annotations

import os
from pathlib import Path


def _load_env() -> None:
    """零依赖加载 backend/.env（不覆盖已存在的环境变量）。"""
    env_file = Path(__file__).resolve().parent / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_env()

# ---- DeepSeek Flash V4.1（原生多模态，OpenAI 兼容）----
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv(
    "DEEPSEEK_BASE_URL", "https://api.deepseek.com/chat/completions"
)
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-flash")

# ---- 通义千问 Qwen-VL（阿里云百炼，OpenAI 兼容）----
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
DASHSCOPE_BASE_URL = os.getenv(
    "DASHSCOPE_BASE_URL",
    "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
)
DASHSCOPE_MODEL = os.getenv("DASHSCOPE_MODEL", "qwen-vl-max-latest")
