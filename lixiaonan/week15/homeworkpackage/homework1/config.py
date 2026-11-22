"""环境变量配置（设计文档 §6、§7）。

模型统一走 OpenAI 兼容协议，通过环境变量切换供应商/模型/实现引擎；
变量名兼容两种命名：LLM_API_KEY / LLM_BASE_URL / LLM_MODEL（优先）
或 OPENAI_API_KEY / OPENAI_BASE_URL / MODEL_NAME（回退）。
启动时由 validate_settings() 校验必填项。
"""

import os

from dotenv import load_dotenv

load_dotenv()


def _pick(*names: str) -> str:
    """按优先级返回第一个非空环境变量的值。"""
    for name in names:
        value = os.getenv(name, "")
        if value.strip():
            return value.strip()
    return ""


# 以下导出名供 pipelines 使用；值本身兼容两种命名
OPENAI_API_KEY = _pick("LLM_API_KEY", "OPENAI_API_KEY")
OPENAI_BASE_URL = _pick("LLM_BASE_URL", "OPENAI_BASE_URL") or "https://api.openai.com/v1"
MODEL_NAME = _pick("LLM_MODEL", "MODEL_NAME") or "gpt-4o-mini"
PIPELINE_ENGINE = os.getenv("PIPELINE_ENGINE", "langgraph").strip().lower()  # langgraph | openai-agents
PORT = int(os.getenv("PORT", "8000"))

VALID_ENGINES = ("langgraph", "openai-agents")


def validate_settings() -> None:
    """启动时校验必填配置项，缺失则抛出明确错误。"""
    missing = [k for k, v in (("LLM_API_KEY / OPENAI_API_KEY", OPENAI_API_KEY),) if not v]
    if missing:
        raise RuntimeError(
            f"缺少必填环境变量：{', '.join(missing)}（请参考 .env.example 配置）"
        )
    if PIPELINE_ENGINE not in VALID_ENGINES:
        raise RuntimeError(
            f"PIPELINE_ENGINE 取值非法：{PIPELINE_ENGINE!r}，可选 {VALID_ENGINES}"
        )
