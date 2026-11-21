"""集中读取环境变量与全局配置。"""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def _fix_ssl_cert_file() -> None:
    """修复 Windows/conda 下 SSL_CERT_FILE 指向不存在文件导致的 FileNotFoundError。

    部分 conda 环境会设置 SSL_CERT_FILE=<env>/ssl/cacert.pem，但该文件可能不存在，
    导致 openai(httpx2) 在创建 SSL context 时崩溃。这里在导入期把它重定向到 certifi。
    """
    cert_file = os.environ.get("SSL_CERT_FILE")
    if cert_file and not os.path.exists(cert_file):
        try:
            import certifi

            os.environ["SSL_CERT_FILE"] = certifi.where()
            os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()
        except ImportError:
            pass


_fix_ssl_cert_file()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
# 使用 OpenAI 兼容服务（如阿里云 DashScope、代理等）时设置
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com").strip() or None
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "deepseek-v4-flash").strip()

# 后端监听
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))

# 默认使用哪套实现：langgraph | openai-agents
DEFAULT_IMPL = os.getenv("DEFAULT_IMPL", "langgraph").strip()


def has_llm() -> bool:
    """是否配置了 LLM。未配置时两个实现都会退化为离线规则/模板。"""
    return bool(OPENAI_API_KEY)
