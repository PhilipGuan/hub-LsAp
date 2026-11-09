"""配置（S1）：环境变量 + 成本上限常量。"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# ---- 密钥与服务 ----
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
BOCHA_API_KEY = os.getenv("BOCHA_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")

# 模型分工：关键词/抽取用快模型，规划/综合用强模型（控制成本）
LLM_MODEL = os.getenv("LLM_MODEL", "qwen-plus")
LLM_MODEL_FAST = os.getenv("LLM_MODEL_FAST", "qwen-flash")
LLM_MODEL_STRONG = os.getenv("LLM_MODEL_STRONG", "qwen-max")

# ---- 成本与上限控制（防止 Agent 无限迭代） ----
MAX_ROUNDS = int(os.getenv("MAX_ROUNDS", "4"))            # 检索-反思最多迭代轮次
PAGES_PER_ROUND = int(os.getenv("PAGES_PER_ROUND", "5"))   # 每子问题每轮最多细读页面数
MAX_TOTAL_PAGES = int(os.getenv("MAX_TOTAL_PAGES", "25"))  # 单次研究总页面上限
SEARCH_COUNT = int(os.getenv("SEARCH_COUNT", "10"))        # 博查每次返回条数
MAX_SUB_QUESTIONS = int(os.getenv("MAX_SUB_QUESTIONS", "6"))  # 规划拆解子问题上限

SEARCH_URL = "https://api.bocha.cn/v1/web-search"
OUTPUT_DIR = PROJECT_ROOT / "output"


def require_keys() -> None:
    """启动前校验必需密钥。"""
    missing = [
        name
        for name, val in (("DASHSCOPE_API_KEY", DASHSCOPE_API_KEY), ("BOCHA_API_KEY", BOCHA_API_KEY))
        if not val
    ]
    if missing:
        raise RuntimeError(f"缺少环境变量：{', '.join(missing)}（请复制 .env.example 为 .env 并填写）")
