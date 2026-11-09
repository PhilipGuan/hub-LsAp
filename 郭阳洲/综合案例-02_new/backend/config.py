# -*- coding: utf-8 -*-
"""配置模块：路径常量 + 从项目根 .env 读取的密钥与参数。

规格见 README.md / 任务说明书.md（T1）。
密钥只从 .env 读取，禁止写死在代码里；.env 不提交。
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- 路径常量
# 全部基于 __file__ 定位，保证在任何工作目录下运行都指向同一位置。
BASE_DIR = Path(__file__).resolve().parent  # backend/
PROJECT_ROOT = BASE_DIR.parent  # 项目根目录
DATA_DIR = BASE_DIR / "data" / "research"  # 研究记录落盘目录
TEMPLATE_DIR = BASE_DIR / "templates"  # Jinja2 提示词模板目录
ENV_FILE = PROJECT_ROOT / ".env"  # 项目根 .env

# override=False：已存在的真实环境变量优先，便于临时覆盖 / CI 注入。
load_dotenv(ENV_FILE, override=False)


def _get_str(name: str, default: str = "") -> str:
    """读取字符串配置项，空值回退默认值。"""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip()


def _get_int(name: str, default: int) -> int:
    """读取整数配置项，缺失或非法时回退默认值（不抛异常，避免启动失败）。"""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("配置项 %s=%r 不是合法整数，回退默认值 %d", name, raw, default)
        return default


# ---------------------------------------------------------------- 配置项
# LLM（DeepSeek，OpenAI 兼容接口）
OPENAI_API_KEY = _get_str("OPENAI_API_KEY")
OPENAI_BASE_URL = _get_str("OPENAI_BASE_URL", "https://api.deepseek.com/")
MODEL_NAME = _get_str("MODEL_NAME", "deepseek-v4-flash")

# 搜索（Bocha 网页搜索）
BOCHA_API_KEY = _get_str("BOCHA_API_KEY")
BOCHA_SEARCH_COUNT = _get_int("BOCHA_SEARCH_COUNT", 10)

# 研究参数
RESEARCH_MAX_ROUNDS = _get_int("RESEARCH_MAX_ROUNDS", 3)
LLM_RETRIES = _get_int("LLM_RETRIES", 3)

# 服务（CORS 允许的前端来源）
FRONTEND_ORIGIN = _get_str("FRONTEND_ORIGIN", "http://localhost:3000")


# ---------------------------------------------------------------- 工具函数
def ensure_data_dir() -> Path:
    """确保 DATA_DIR 存在（幂等），返回该目录路径。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR


def today_str() -> str:
    """返回本地日期，格式 YYYY-MM-DD。"""
    return datetime.now().strftime("%Y-%m-%d")


def _mask(secret: str) -> str:
    """密钥脱敏：只保留尾 4 位，用于自检打印。"""
    if not secret:
        return "(未设置)"
    if len(secret) <= 4:
        return "*" * len(secret)
    return "*" * (len(secret) - 4) + secret[-4:]


# ---------------------------------------------------------------- 自检 demo
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    print("=== 路径常量 ===")
    print(f"PROJECT_ROOT  = {PROJECT_ROOT}")
    print(f"BASE_DIR      = {BASE_DIR}")
    print(f"DATA_DIR      = {DATA_DIR}")
    print(f"TEMPLATE_DIR  = {TEMPLATE_DIR}")
    print(f"ENV_FILE      = {ENV_FILE} (存在: {ENV_FILE.exists()})")

    print("=== 配置项 ===")
    print(f"OPENAI_API_KEY     = {_mask(OPENAI_API_KEY)}")
    print(f"OPENAI_BASE_URL    = {OPENAI_BASE_URL}")
    print(f"MODEL_NAME         = {MODEL_NAME}")
    print(f"BOCHA_API_KEY      = {_mask(BOCHA_API_KEY)}")
    print(f"BOCHA_SEARCH_COUNT = {BOCHA_SEARCH_COUNT}")
    print(f"RESEARCH_MAX_ROUNDS= {RESEARCH_MAX_ROUNDS}")
    print(f"LLM_RETRIES        = {LLM_RETRIES}")
    print(f"FRONTEND_ORIGIN    = {FRONTEND_ORIGIN}")

    created = ensure_data_dir()
    print(f"数据目录就绪      = {created} (存在: {created.exists()})")
    print(f"today_str()       = {today_str()}")

    assert MODEL_NAME, "MODEL_NAME 不应为空"
    assert BOCHA_SEARCH_COUNT > 0, "BOCHA_SEARCH_COUNT 应为正数"
    assert RESEARCH_MAX_ROUNDS >= 1, "RESEARCH_MAX_ROUNDS 至少为 1"
    assert LLM_RETRIES >= 1, "LLM_RETRIES 至少为 1"
    assert created.is_dir(), "ensure_data_dir() 应创建出目录"

    print("配置自检 OK")
