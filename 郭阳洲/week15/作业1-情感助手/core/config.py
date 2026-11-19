"""作业1 · 核心层：环境配置与 LLM 客户端工厂。

所有层（storage/flows/api/frontend）统一从这里读取配置，
保证两个框架后端使用完全一致的模型与存储参数。
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# 项目目录：作业1-情感助手/（本文件位于 core/ 下）
PROJECT_DIR = Path(__file__).resolve().parent.parent
# .env 位于上一级项目根 openai-agents-sandbox/
ROOT_DIR = PROJECT_DIR.parent
load_dotenv(ROOT_DIR / ".env")

BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com")
API_KEY = os.getenv("OPENAI_API_KEY", "")
MODEL = os.getenv("OPENAI_MODEL", "deepseek-flash")

# ---- 存储 ----
# SQLite 数据库文件（运行时自动创建）
DB_PATH = PROJECT_DIR / "app.db"

# 短期记忆窗口：注入上下文的最近消息条数（可用 .env 覆盖）
MEMORY_WINDOW = int(os.getenv("MEMORY_WINDOW", "10"))

# 会话标题：取首条输入的前 N 个字符
TITLE_LENGTH = 20

# 长期记忆容量上限
MAX_KEY_EVENTS = 10
MAX_TOPICS = 10

# ---- 端口 ----
LANGGRAPH_PORT = 8001
AGENTS_PORT = 8002


def get_openai_client():
    """同步 OpenAI 兼容客户端（openai SDK 使用）。"""
    from openai import OpenAI

    return OpenAI(api_key=API_KEY, base_url=BASE_URL)


def get_async_openai_client():
    """异步 OpenAI 兼容客户端（openai-agents 使用）。"""
    from openai import AsyncOpenAI

    return AsyncOpenAI(api_key=API_KEY, base_url=BASE_URL)


def get_chat_openai(temperature: float = 0.0):
    """langchain-openai 的 ChatOpenAI（LangGraph 使用）。"""
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=MODEL,
        api_key=API_KEY,
        base_url=BASE_URL,
        temperature=temperature,
    )