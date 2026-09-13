# -*- coding: utf-8 -*-
"""envify-llm 规范的环境变量加载（继承综合案例-02 的方案，对齐 Philip 偏好）。
级联查找：脚本同目录 .env → 父目录 .env → 向上父目录的父目录（Week08/.env 优先 override）。
__main__ 运行时打印 [envify ✅] 自检串。
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
LOCAL_ENV = BASE_DIR.parent / ".env"
PROJECT_ROOT_ENV = BASE_DIR.parent.parent.parent / ".env"    # 等价于 Week08/.env（按路径自定位）
WEEK08_ENV_FALLBACK = BASE_DIR.parent.parent.parent.parent / "Week08" / ".env"

_CFG_MAP: dict[str, dict[str, str]] = {
    "deepseek": {
        "api_key_var":  "DEEPSEEK_API_KEY",
        "base_url_var": "DEEPSEEK_BASE_URL",
        "model_var":    "DEEPSEEK_MODEL",
        "default_base": "https://api.deepseek.com/v1",
        "default_model":"deepseek-v4-flash",
    },
    "qwen": {
        "api_key_var":  "QWEN_API_KEY",
        "base_url_var": "QWEN_BASE_URL",
        "model_var":    "QWEN_MODEL",
        "default_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model":"qwen-plus",
    },
    "openai": {
        "api_key_var":  "OPENAI_API_KEY",
        "base_url_var": "OPENAI_BASE_URL",
        "model_var":    "OPENAI_MODEL",
        "default_base": "https://api.openai.com/v1",
        "default_model":"gpt-4o-mini",
    },
}

_SK_PHRASES = ["sk-xxx", "sk_xxx", "sk_test_xxx", "sk-proj-xxx"]


def _read_env_cascaded() -> None:
    """envify-llm §2.3 级联查找 · 越近优先级越高，Week08 高优 override。"""
    load_dotenv(BASE_DIR.parent / ".env")                 # 1) etf-research-agent/.env （用户自己放的）
    load_dotenv(BASE_DIR.parent.parent / ".env", override=True)  # 2) 综合案例-02 VibeCoding-Philip/.env
    load_dotenv(PROJECT_ROOT_ENV, override=True)          # 3) Week08/.env（Philip 的主环境）
    load_dotenv(WEEK08_ENV_FALLBACK, override=True)       # 4) 兜底 Week08/.env（目录不同时）


def _validate_key(provider: str, key: str) -> None:
    """envify-llm §2.4 三条安全校验：空 Key / 占位符 your_* / 示例 sk-xxx。"""
    if not key or not key.strip():
        raise RuntimeError(
            f"[envify ❌] LLM_PROVIDER={provider}，但 {_CFG_MAP[provider]['api_key_var']} 为空。"
            f"请检查 .env （推荐优先在 Week08/.env 中配置）。"
        )
    stripped = key.strip()
    low = stripped.lower()
    if low.startswith("your_") or low.startswith("你的") or low.startswith("demo_"):
        raise RuntimeError(
            f"[envify ❌] Key 还是占位符（your_/你的_/demo_ 前缀）。请把真实 Key 填入 .env 后重试。"
        )
    for phrase in _SK_PHRASES:
        if phrase in low:
            raise RuntimeError(
                f"[envify ❌] Key 还是示例值（包含 {phrase}）。请把真实 Key 填入 .env 后重试。"
            )


def load_env_and_validate() -> tuple[str, str, str, str]:
    """envify-llm 主入口：级联加载 + 校验 + 对齐写回 os.environ OPENAI_* 三件套。

    Returns:
        (provider, api_key, base_url, model_name)
    """
    _read_env_cascaded()
    provider_from_env = (os.getenv("LLM_PROVIDER") or "").strip().lower()
    if provider_from_env in _CFG_MAP:
        provider = provider_from_env
    elif os.getenv("OPENAI_API_KEY") and (os.getenv("OPENAI_API_KEY") or "").strip():
        # 向后兼容：老环境只填了 OPENAI_* 三件套，没设 LLM_PROVIDER → 自动判为 openai
        provider = "openai"
    else:
        provider = "deepseek"
    if provider not in _CFG_MAP:
        raise RuntimeError(
            f"[envify ❌] LLM_PROVIDER={provider!r} 不支持（支持 {list(_CFG_MAP)}）。"
        )
    cfg = _CFG_MAP[provider]
    key = (os.getenv(cfg["api_key_var"]) or "").strip()
    base = (os.getenv(cfg["base_url_var"]) or cfg["default_base"]).strip().rstrip("/")
    model = (os.getenv(cfg["model_var"]) or cfg["default_model"]).strip()
    _validate_key(provider, key)

    # 对齐写回：给 openai-agents SDK / 综合案例-02 老代码自动读
    os.environ["OPENAI_API_KEY"] = key
    os.environ["OPENAI_BASE_URL"] = base
    os.environ.setdefault("OPENAI_MODEL", model)
    return provider, key, base, model


# ============================================================
# 业务常量（Dry-Run 占位，正式实现时微调）
# ============================================================
LLM_PROVIDER, _LLM_API_KEY, LLM_BASE_URL, LLM_MODEL = load_env_and_validate()
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.7"))
RESEARCH_MAX_ROUNDS = int(os.getenv("RESEARCH_MAX_ROUNDS", "3"))
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "*")

# Bocha（沿用综合案例-02 的搜索引擎接入）
BOCHA_BASE_URL = os.getenv("BOCHA_BASE_URL", "https://api.bochaai.com/v1")
BOCHA_API_KEY = os.getenv("BOCHA_API_KEY", "")

DATA_DIR = BASE_DIR.parent / "data" / "etf_records"
CACHE_DIR = BASE_DIR.parent / "cache"
SAMPLES_DIR = BASE_DIR.parent / "samples"
DATA_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)
SAMPLES_DIR.mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.info(
        "[envify ✅] LLM_PROVIDER=%s API前6位=%s BASE_URL=%s MODEL=%s",
        LLM_PROVIDER,
        _LLM_API_KEY[:6],
        LLM_BASE_URL,
        LLM_MODEL,
    )
    logging.info("[envify ✅] DATA_DIR=%s | CACHE_DIR=%s | MAX_ROUNDS=%d",
                 DATA_DIR, CACHE_DIR, RESEARCH_MAX_ROUNDS)
    sys.exit(0)
