"""配置层：.env 查找、模型设置、数据库定位。

与原文对照（`sql-agent-workflow.ipynb` 第 0 节「环境准备」）：

| 原实现 | 本实现 | 差异说明 |
|---|---|---|
| `find_up(".env")` + `load_dotenv(override=True)` | `load_env()` | 逻辑照搬 |
| 把 Key 伪装成 `OPENAI_API_KEY` 写进 `os.environ` | **不做** | LangChain 的 `ChatOpenAI` 支持显式传参，
  不再需要污染全局环境变量（见 `llm.py`） |
| `find_up("chinook.db")` → `DB_URL` | `get_db_url()` | 逻辑照搬，并支持用 `DB_URL` 环境变量覆盖 |
| `_redact()` | `redact()` | 逻辑照搬，但改为**全局替换**家目录（原文只处理以家目录开头的 `Path`，
  本项目的调用点会传入 `sqlite:////Users/...` 连接串，按原文写法会漏脱敏） |
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


class ConfigError(RuntimeError):
    """配置缺失时抛出，报错信息里只说「缺什么」，不回显任何密钥。"""


def find_up(filename: str) -> Path | None:
    """从当前工作目录逐级向上查找文件，返回第一个命中的绝对路径（照搬原实现）。"""
    here = Path.cwd().resolve()
    for p in [here, *here.parents]:
        if (p / filename).exists():
            return p / filename
    return None


def load_env() -> Path:
    """.env 逐级向上查找并加载；找不到直接报错，避免「以为读到了其实没读到」。"""
    env_path = find_up(".env")
    if env_path is None:
        raise ConfigError("没找到 .env：请在项目根目录放置 .env（可从 .env.example 复制）")
    load_dotenv(env_path, override=True)
    return env_path


def redact(p: str | Path) -> str:
    """把家目录替换成 ~，避免把本机用户名 / 绝对路径写进输出。

    与原文的唯一差别：原文只处理「以家目录开头」的 `Path`；本实现改为**全局替换**。
    因为本项目的调用点还会传入 `sqlite:////Users/...` 这类**连接串**（不以家目录开头），
    按原文写法会漏脱敏（已在 notebook 输出里复现过）。改动只让脱敏更彻底，不改变可用结果。
    """
    return str(p).replace(str(Path.home()), "~")


@dataclass(frozen=True)
class LLMSettings:
    """一次 LLM 接入所需的全部信息（provider 无关）。"""

    provider: str
    model: str
    api_key: str
    base_url: str

    def describe(self) -> str:
        """脱敏描述，可直接打印/写进 notebook 输出。"""
        return f"provider={self.provider} model={self.model} base_url={self.base_url}"


# 各 provider 对应的环境变量名（`.env` 里已按此命名）
_PROVIDERS = {
    "deepseek": ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL",
                 "https://api.deepseek.com/v1", "deepseek-v4-flash"),
    "qwen": ("QWEN_API_KEY", "QWEN_BASE_URL", "QWEN_MODEL",
             "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
}


def get_llm_settings(provider: str | None = None) -> LLMSettings:
    """按 `LLM_PROVIDER`（或显式入参）组装模型设置。

    这是原实现「把 DeepSeek 伪装成 OpenAI 接口」那几行的 LangChain 等价物：
    这里只负责**产出配置**，不再改写 `os.environ`。
    """
    load_env()
    provider = (provider or os.environ.get("LLM_PROVIDER") or "deepseek").strip().lower()
    if provider not in _PROVIDERS:
        raise ConfigError(f"不支持的 LLM_PROVIDER={provider!r}，可选：{sorted(_PROVIDERS)}")

    key_var, base_var, model_var, default_base, default_model = _PROVIDERS[provider]
    api_key = os.environ.get(key_var, "").strip()
    if not api_key or api_key.startswith(("你的_", "sk-你的")):
        raise ConfigError(f"环境变量 {key_var} 未配置（请检查项目根目录的 .env）")

    return LLMSettings(
        provider=provider,
        model=os.environ.get(model_var, default_model),
        api_key=api_key,
        base_url=os.environ.get(base_var, default_base),
    )


def get_db_url(db_file: str = "chinook.db") -> str:
    """定位数据库：`DB_URL` 环境变量优先，否则按文件名逐级向上找（照搬原实现）。"""
    override = os.environ.get("DB_URL", "").strip()
    if override:
        return override
    db_path = find_up(db_file)
    if db_path is None:
        raise ConfigError(f"没找到 {db_file}：请把数据库文件放在项目目录下")
    return f"sqlite:///{db_path}"
