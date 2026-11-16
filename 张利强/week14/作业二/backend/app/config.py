from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STORAGE_ROOT = PROJECT_ROOT / "storage"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 智谱
    zhipu_api_key: str = ""
    llm_model: str = "glm-4v-flash"
    # 主模型遇到 429（免费模型高峰限流）时的兜底模型；留空表示不兜底
    llm_fallback_model: str = ""
    embed_model: str = "embedding-3"
    embed_dim: int = 1024

    # Kafka
    kafka_bootstrap: str = "127.0.0.1:9092"
    kafka_topic_parse: str = "pdf-parse"
    kafka_topic_dlq: str = "pdf-parse-dlq"
    kafka_topic_status: str = "doc-status"

    # Elasticsearch
    es_url: str = "http://127.0.0.1:9200"
    es_index: str = "rag_chunks_v1"

    # MinerU
    mineru_bin: str = "/opt/anaconda3/envs/py312/bin/mineru"
    mineru_tier: str = "flash"
    mineru_timeout: int = 1800

    # 业务参数
    max_upload_mb: int = 50
    chunk_size: int = 800
    chunk_overlap: int = 100
    top_k: int = 6

    # 调试
    mock_llm: bool = False
    mock_embedding: bool = False

    @property
    def use_mock_llm(self) -> bool:
        return self.mock_llm or not self.zhipu_api_key

    @property
    def use_mock_embedding(self) -> bool:
        return self.mock_embedding or not self.zhipu_api_key

    @property
    def upload_dir(self) -> Path:
        return STORAGE_ROOT / "uploads"

    @property
    def chat_dir(self) -> Path:
        return STORAGE_ROOT / "chat"

    @property
    def parsed_dir(self) -> Path:
        return STORAGE_ROOT / "parsed"

    @property
    def db_path(self) -> Path:
        return STORAGE_ROOT / "app.db"

    def ensure_dirs(self) -> None:
        for p in (STORAGE_ROOT, self.upload_dir, self.chat_dir, self.parsed_dir):
            p.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
