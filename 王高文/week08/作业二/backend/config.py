"""配置：从项目根 .env 读取（DeepSeek / Bocha key、MAX_ROUNDS 等）。

用 pydantic-settings 读取项目根目录的 `.env`；字段名大小写不敏感
（`DEEPSEEK_API_KEY` -> `deepseek_api_key`）。BASE_URL 里末尾的 `/` 由
`_normalize_base_url` 保证。
"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = None  # 延迟到模块内 logger，避免循环导入

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class _Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(Path(PROJECT_ROOT / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # DeepSeek
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com/"
    deepseek_model: str = "deepseek-v4-flash"

    # Bocha
    bocha_api_key: str = "sk-3d2293ad83aa4823a7c7ce8dd5ff8c72"
    bocha_endpoint: str = "https://api.bocha.cn/v1/web-search"
    bocha_default_count: int = 10

    # 研究控制
    llm_retries: int = 3
    research_max_rounds: int = 3
    data_dir: str = "backend/data"

    def _normalize_base_url(self, value: str) -> str:
        value = value.strip()
        if not value.endswith("/"):
            value += "/"
        return value

    @property
    def deepseek_base_url_normalized(self) -> str:
        return self._normalize_base_url(self.deepseek_base_url)


settings = _Settings()


def _mask(key: str) -> str:
    """脱敏显示密钥：只保留前后 4 位。"""
    if not key:
        return "<未配置>"
    if len(key) <= 8:
        return "***"
    return f"{key[:4]}...{key[-4:]}"


if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    print("=== 配置自检 ===")
    print("DEEPSEEK_MODEL        :", settings.deepseek_model)
    print("DEEPSEEK_BASE_URL     :", settings.deepseek_base_url_normalized)
    print("DEEPSEEK_API_KEY      :", _mask(settings.deepseek_api_key))
    print("BOCHA_API_KEY         :", _mask(settings.bocha_api_key))
    print("BOCHA_ENDPOINT        :", settings.bocha_endpoint)
    print("LLM_RETRIES           :", settings.llm_retries)
    print("RESEARCH_MAX_ROUNDS   :", settings.research_max_rounds)
    print("DATA_DIR              :", settings.data_dir)