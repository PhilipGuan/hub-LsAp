"""集中读取项目根目录 `.env` 中的运行配置。

所有进程（情感识别 / 响应 / API）都从这里取配置，避免各文件重复读环境变量。
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
# 本地运行：.env 是本项目唯一配置来源，同名 shell 变量（可能是过期值）不覆盖它。
# 容器运行：docker-compose 通过 env_file 注入配置，并设 DOTENV_OVERRIDE=false，
# 让编排层传入的 KAFKA_BOOTSTRAP / REDIS_URL 等优先（.env 本身不会打进镜像）。
_DOTENV_OVERRIDE = os.getenv("DOTENV_OVERRIDE", "true").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
load_dotenv(BASE_DIR / ".env", override=_DOTENV_OVERRIDE)

# ---- Qwen（阿里云百炼 DashScope，OpenAI 兼容接口） ----
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "").strip()
QWEN_BASE_URL = os.getenv(
    "QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
).strip()
QWEN_MODEL = os.getenv("QWEN_MODEL", "qwen-plus").strip()

# ---- 基础设施 ----
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "localhost:9092").strip()
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0").strip()

# ---- 业务参数 ----
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.7"))
MAX_CLARIFICATION_ROUNDS = int(os.getenv("MAX_CLARIFICATION_ROUNDS", "2"))
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "3600"))
LLM_TIMEOUT_SECONDS = int(os.getenv("LLM_TIMEOUT_SECONDS", "120"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# ---- 追踪（仅 OpenAI 平台导出需要；使用 Qwen 时可留空自动关闭） ----
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
AGENTS_TRACING_DISABLED = (
    os.getenv("AGENTS_TRACING_DISABLED", "").strip().lower()
    in {"1", "true", "yes", "on"}
    or not OPENAI_API_KEY
)

if not 0.0 <= CONFIDENCE_THRESHOLD <= 1.0:
    raise ValueError("CONFIDENCE_THRESHOLD 必须在 0~1 之间")
if MAX_CLARIFICATION_ROUNDS < 0:
    raise ValueError("MAX_CLARIFICATION_ROUNDS 不能为负数")


def require_api_key() -> None:
    """进程启动时快速失败，避免跑到第一次 LLM 调用才发现没配 key。"""
    if not DASHSCOPE_API_KEY:
        raise RuntimeError(
            "DASHSCOPE_API_KEY 未配置：请在项目根目录 .env 中填写 Qwen（DashScope）API Key，"
            "参考 .env.example。"
        )
