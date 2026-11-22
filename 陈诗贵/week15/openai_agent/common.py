# -*- coding: utf-8 -*-
"""公共层：端口常量、.env 加载、OpenAI Agents SDK 初始化、JSON 提取。

与 langgraph 版完全独立，端口整体错开到 5010~5013，两套可同时运行。
"""
import json
import os
import re
from typing import Any, Dict, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ---- 端口约定（与 README 第 2 节一致）----
BACKEND_PORT = 5010
FRONTEND_PORT = 5011
SENTIMENT_PORT = 5012
REPLY_PORT = 5013

# 下游 HTTP 超时（秒）
RPC_TIMEOUT = 180

# 监听地址：本地开发默认只开 127.0.0.1；容器内通过 BIND_HOST=0.0.0.0 才能被端口映射访问
BIND_HOST = os.getenv("BIND_HOST", "127.0.0.1")


def _load_env() -> Optional[str]:
    """从项目目录向上最多 6 层找 .env（openai_agent → week15 → 作业 → root）。"""
    from dotenv import load_dotenv

    d = BASE_DIR
    for _ in range(6):
        path = os.path.join(d, ".env")
        if os.path.exists(path):
            load_dotenv(path, override=False)
            return path
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return None


ENV_PATH = _load_env()

LLM_API_KEY = os.getenv("DS_API_KEY") or os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-flash")

_sdk_ready = False


def has_api_key() -> bool:
    return bool(LLM_API_KEY)


def init_sdk() -> None:
    """初始化 Agents SDK（幂等）。

    三件事缺一不可：
    1. 指向 DeepSeek 兼容端点；
    2. 强制 chat_completions 协议（DeepSeek 不支持 Responses API）；
    3. 关闭 tracing（DeepSeek key 无法上报 OpenAI trace，否则每个请求打 401 噪音）。
    """
    global _sdk_ready
    if _sdk_ready:
        return

    from openai import AsyncOpenAI
    from agents import (
        set_default_openai_api,
        set_default_openai_client,
        set_tracing_disabled,
    )

    set_default_openai_client(
        AsyncOpenAI(api_key=LLM_API_KEY or "missing", base_url=LLM_BASE_URL)
    )
    set_default_openai_api("chat_completions")
    set_tracing_disabled(True)
    _sdk_ready = True


def error_text(exc: Exception) -> str:
    """异常转成一行可读错误，防止把整段 traceback 塞给前端。"""
    msg = str(exc).replace("\n", " ").strip()
    return f"{type(exc).__name__}: {msg[:300]}"


def extract_json(text: str) -> Optional[Dict[str, Any]]:
    """从模型回复里抠出第一个 JSON 对象，兼容 ```json 代码块和前后废话。"""
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.S)
    candidate = fenced.group(1) if fenced else text
    start, end = candidate.find("{"), candidate.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(candidate[start:end + 1])
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def clip_text(text: str, limit: int = 2000) -> str:
    """限制输入长度，防止超长文本把 token 打爆。"""
    return (text or "").strip()[:limit]
