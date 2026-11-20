"""全局配置（pydantic-settings）。

读取优先级：环境变量 > .env 文件（backend/.env，其次项目根目录 .env）。
所有配置项与根目录 .env.example 一一对应。
"""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录：backend/app/core/config.py -> homework2/
PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---------- 服务 ----------
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    log_level: str = "INFO"

    # ---------- Kafka（功能1：任务分发，F1-5/F1-6） ----------
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_topic_parse_task: str = "pdf-parse-task"
    kafka_consumer_group: str = "pdf-parse-worker"
    kafka_max_retry: int = 3
    stale_parsing_minutes: int = 30        # F2-4：PARSING 停留超过该分钟数则标记失败并重新入队（0=禁用）
    stale_check_interval_seconds: int = 60 # 超时兜底检查周期

    # ---------- 数据库（§4） ----------
    database_url: str = "sqlite:///./data/homework2.db"

    # ---------- 存储（F1-3/F1-8） ----------
    storage_backend: str = "local"  # local | minio
    data_dir: str = "./data"        # 本地模式根目录
    static_url_prefix: str = "/static"
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "homework2"

    # ---------- 解析（F1-7） ----------
    parser_backend: str = "mineru"  # mineru | pymupdf（pymupdf 为无 GPU/未装 MinerU 时的降级方案）
    # MinerU 4.x 语法：需先 `mineru server start`（本地引擎）；2.x 旧语法为
    # "mineru -p {input} -o {output} -b pipeline"。可整体覆盖，含 {input}/{output} 占位符
    mineru_cmd: str = "mineru parse {input} -o {output}/result.md --pages all --force --wait 540"
    mineru_timeout_seconds: int = 900
    mineru_model_source: str = "modelscope"  # 模型权重下载源 modelscope|huggingface|local；子进程注入
    chunk_min_chars: int = 300      # 文本块最小长度（分块参数）
    chunk_max_chars: int = 1200     # 文本块最大长度

    # ---------- 上传限制（F1-2 / F1-9） ----------
    max_upload_size_mb: int = 50
    max_upload_count: int = 10
    allow_duplicate: bool = True

    # ---------- 向量化（F1-8 / F3-3 检索） ----------
    embedding_enabled: bool = False
    embedding_model: str = "BAAI/bge-small-zh-v1.5"   # 轻量中文模型（512 维）；检索质量要求高可换 BAAI/bge-m3（1024 维）
    embedding_dim: int = 512
    hf_endpoint: str = "https://hf-mirror.com"        # HuggingFace 国内镜像，留空则不设置
    embedding_image_caption: bool = False             # 解析时调用多模态 LLM 生成图片描述（需要真实 LLM）

    # ---------- 多模态 LLM（功能3 / F3-4） ----------
    llm_backend: str = "mock"                # openai（OpenAI 兼容协议）| mock（本地桩，无需 Key，用于联调）
    llm_api_base: str = "https://open.bigmodel.cn/api/paas/v4"  # 智谱 OpenAI 兼容端点；dashscope 为 compatible-mode/v1
    llm_api_key: str = ""
    llm_model: str = "glm-4.5v"              # 必须为多模态模型（如 glm-4.5v / qwen2.5-vl-*）
    llm_temperature: float = 0.7
    llm_max_tokens: int = 2048
    llm_thinking_effort: str = "low"  # 思考模型档位 low/high/max；留空则不传该参数（非思考模型需置空）

    # ---------- 检索（F3-3） ----------
    retrieval_top_k: int = 5

    # ---------- 派生路径 ----------
    @property
    def data_path(self) -> Path:
        """数据根目录的绝对路径（相对路径以项目根为基准，避免受启动 CWD 影响）。"""
        p = Path(self.data_dir)
        return p if p.is_absolute() else (PROJECT_ROOT / p).resolve()

    @property
    def upload_dir(self) -> Path:
        return self.data_path / "uploads"

    @property
    def markdown_dir(self) -> Path:
        return self.data_path / "markdown"

    @property
    def image_dir(self) -> Path:
        return self.data_path / "images"

    @property
    def vector_store_dir(self) -> Path:
        return self.data_path / "vector_store"

    @property
    def logs_path(self) -> Path:
        return self.data_path / "logs"

    def ensure_dirs(self) -> None:
        for d in (self.data_path, self.upload_dir, self.markdown_dir,
                  self.image_dir, self.vector_store_dir, self.logs_path):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
