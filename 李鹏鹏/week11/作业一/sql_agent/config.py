"""配置加载。

优先级：进程环境变量 > 同目录/上级目录的 .env 文件 > 默认值。

支持的大模型服务：
    - 阿里云百炼 DashScope（默认，OpenAI 兼容协议）
    - 任何兼容 OpenAI Chat Completions 协议的服务
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen-plus"


class ConfigError(RuntimeError):
    """配置缺失或不合法。"""


def _load_dotenv(env_file: Optional[Path] = None) -> None:
    """极简 .env 解析，避免额外依赖 python-dotenv。

    只做 KEY=VALUE 解析，已存在的环境变量不会被覆盖。
    """
    candidates = []
    if env_file is not None:
        candidates.append(Path(env_file))
    else:
        here = Path(__file__).resolve().parent          # sql_agent/
        candidates.extend([here / ".env", here.parent / ".env"])

    for path in candidates:
        if not path.is_file():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                os.environ.setdefault(key, value)


def _first_env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return default


@dataclass
class Settings:
    """运行时配置。"""

    api_key: str = ""
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    temperature: float = 0.0
    timeout: float = 60.0
    max_rows: int = 100          # 返回给用户/模型的最大行数
    max_repair: int = 3          # SQL 校验/执行失败后的最大改写次数
    query_timeout: float = 30.0  # 单条 SQL 的执行超时（秒）
    language: str = "中文"

    @classmethod
    def load(cls, env_file: Optional[str] = None) -> "Settings":
        _load_dotenv(Path(env_file) if env_file else None)

        api_key = _first_env(
            "SQL_AGENT_API_KEY",
            "DASHSCOPE_API_KEY",
            "OPENAI_API_KEY",
        )
        base_url = _first_env(
            "SQL_AGENT_BASE_URL",
            "OPENAI_BASE_URL",
            default=DEFAULT_BASE_URL,
        )
        model = _first_env("SQL_AGENT_MODEL", "OPENAI_MODEL", default=DEFAULT_MODEL)

        def _as_float(name: str, default: float) -> float:
            raw = os.environ.get(name, "").strip()
            if not raw:
                return default
            try:
                return float(raw)
            except ValueError:
                return default

        def _as_int(name: str, default: int) -> int:
            raw = os.environ.get(name, "").strip()
            if not raw:
                return default
            try:
                return int(raw)
            except ValueError:
                return default

        return cls(
            api_key=api_key,
            base_url=base_url.rstrip("/"),
            model=model,
            temperature=_as_float("SQL_AGENT_TEMPERATURE", 0.0),
            timeout=_as_float("SQL_AGENT_TIMEOUT", 60.0),
            max_rows=max(1, _as_int("SQL_AGENT_MAX_ROWS", 100)),
            max_repair=max(0, _as_int("SQL_AGENT_MAX_REPAIR", 3)),
            query_timeout=_as_float("SQL_AGENT_QUERY_TIMEOUT", 30.0),
            language=os.environ.get("SQL_AGENT_LANGUAGE", "中文").strip() or "中文",
        )

    def require_api_key(self) -> None:
        if not self.api_key:
            raise ConfigError(
                "未配置大模型 API Key。\n"
                "请设置环境变量 SQL_AGENT_API_KEY（或 DASHSCOPE_API_KEY / OPENAI_API_KEY），\n"
                "或在 04_SQL-Code-Agent-Demo/.env 中写入：\n"
                "    SQL_AGENT_API_KEY=sk-xxxx\n"
                "    SQL_AGENT_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1\n"
                "    SQL_AGENT_MODEL=qwen-plus"
            )
