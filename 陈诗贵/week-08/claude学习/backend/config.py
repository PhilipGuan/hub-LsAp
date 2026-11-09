"""全局配置：从环境变量读取，支持项目根 .env 兜底。"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# 项目根（backend 的上一级），.env 放在这里
_ROOT = Path(__file__).resolve().parent
load_dotenv(_ROOT / ".env")

# ---------- 搜索工具（Bocha） ----------
BOCHA_API_URL = "https://api.bocha.cn/v1/web-search"
BOCHA_API_KEY = os.getenv("BOCHA_API_KEY", "sk-3d2293ad83aa4823a7c7ce8dd5ff8c72")
BOCHA_COUNT = int(os.getenv("BOCHA_COUNT", "10"))

# ---------- LLM（DeepSeek，OpenAI 兼容） ----------
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# ---------- 研究循环参数 ----------
MAX_ROUNDS = int(os.getenv("MAX_ROUNDS", "3"))          # 总检索轮次上限（首轮 + 补检）
MIN_SUB_QUESTIONS = int(os.getenv("MIN_SUB_QUESTIONS", "3"))
MAX_SUB_QUESTIONS = int(os.getenv("MAX_SUB_QUESTIONS", "5"))

# ---------- 产物目录 ----------
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", str(_ROOT.parent / "output")))
