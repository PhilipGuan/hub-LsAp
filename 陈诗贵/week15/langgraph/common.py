# -*- coding: utf-8 -*-
"""公共层：端口常量、.env 加载、LLM 客户端、JSON 提取。

四个进程都从这里取配置，避免各服务重复读环境变量。
"""
import json
import os
import re
from typing import Any, Dict, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ---- 端口约定（与 README 第 2 节一致）----
FRONTEND_PORT = 5001
BACKEND_PORT = 5000
SENTIMENT_PORT = 5002
REPLY_PORT = 5003

# 下游 LLM 调用超时（秒）
LLM_TIMEOUT = 60
# 进程间 HTTP 超时（秒），略大于 LLM 超时，避免外层先掐断
RPC_TIMEOUT = 90


def _load_env() -> Optional[str]:
    """从项目目录向上最多 6 层找 .env（当前在 作业/week15/langgraph，root/.env 在三级之上）。"""
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


def has_api_key() -> bool:
    return bool(LLM_API_KEY)


def get_llm(temperature: float = 0.0):
    """构造 ChatOpenAI。trust_env=False 绕过系统代理，避免本地代理导致 TLS 握手失败。"""
    import httpx
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=LLM_MODEL,
        base_url=LLM_BASE_URL,
        api_key=LLM_API_KEY,
        temperature=temperature,
        http_client=httpx.Client(trust_env=False, timeout=LLM_TIMEOUT),
    )


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
    text = (text or "").strip()
    return text[:limit]
