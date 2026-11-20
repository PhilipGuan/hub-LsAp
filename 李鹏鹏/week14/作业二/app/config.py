"""全局配置。

所有参数都可以通过环境变量或项目根目录的 ``.env`` 覆盖，
这样同一份代码在本地演示、实验环境、服务器上都能直接跑。

约定
----
* 以 ``LLM_`` 开头的变量是通用大模型配置（OpenAI 兼容接口）。
* 同时兼容按服务商命名的写法，例如 ``QWEN_API_KEY`` / ``DEEPSEEK_API_KEY``，
  方便复用其它项目里的 .env。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:  # python-dotenv 是可选依赖：没有它也不影响用真实环境变量启动
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover - 极简环境兜底
    def load_dotenv(*_args: Any, **_kwargs: Any) -> bool:  # type: ignore[misc]
        return False


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


# --------------------------------------------------------------------------- #
# 环境变量读取小工具
# --------------------------------------------------------------------------- #
def _str(key: str, default: str = "") -> str:
    value = os.getenv(key)
    if value is None:
        return default
    value = value.strip()
    return value or default


def _int(key: str, default: int) -> int:
    try:
        return int(_str(key, str(default)))
    except ValueError:
        return default


def _float(key: str, default: float) -> float:
    try:
        return float(_str(key, str(default)))
    except ValueError:
        return default


def _bool(key: str, default: bool) -> bool:
    value = _str(key, "1" if default else "0").lower()
    return value in {"1", "true", "yes", "y", "on"}


def _path(key: str, default: Path) -> Path:
    raw = _str(key)
    return Path(raw).expanduser().resolve() if raw else default.resolve()


# --------------------------------------------------------------------------- #
# 大模型服务商预设：只填 API Key 即可用
# --------------------------------------------------------------------------- #
PROVIDER_PRESETS: dict[str, dict[str, str]] = {
    "qwen": {
        "label": "通义千问 VL（阿里云百炼）",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-vl-max-latest",
        "vision_model": "qwen-vl-max-latest",
        "key_env": "QWEN_API_KEY,DASHSCOPE_API_KEY",
        "vision": "1",
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "vision_model": "gpt-4o-mini",
        "key_env": "OPENAI_API_KEY",
        "vision": "1",
    },
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "vision_model": "",
        "key_env": "DEEPSEEK_API_KEY",
        "vision": "0",
    },
    "custom": {
        "label": "自定义 OpenAI 兼容服务",
        "base_url": "",
        "model": "",
        "vision_model": "",
        "key_env": "",
        "vision": "1",
    },
}


def _preset_key(preset: dict[str, str]) -> str:
    for name in filter(None, preset.get("key_env", "").split(",")):
        value = _str(name)
        if value:
            return value
    return ""


@dataclass(slots=True)
class Settings:
    """运行期配置快照。"""

    # ---------------- 站点 ----------------
    host: str = "0.0.0.0"  # noqa: S104 - 演示需要局域网访问
    port: int = 8000
    open_browser: bool = False
    debug: bool = False

    # ---------------- 目录 ----------------
    data_dir: Path = BASE_DIR / "data"
    static_dir: Path = BASE_DIR / "static"
    max_upload_mb: int = 60
    allowed_ext: tuple[str, ...] = (".pdf",)

    # ---------------- Kafka ----------------
    kafka_enabled: bool = True
    kafka_bootstrap: str = "172.30.0.4:9094"
    kafka_parse_topic: str = "rag.doc.parse"
    kafka_status_topic: str = "rag.doc.status"
    kafka_group: str = "rag-parser"
    kafka_partitions: int = 3
    kafka_replicas: int = 1
    kafka_auto_create: bool = True
    #: Kafka 不可用时，是否退化为进程内直连队列（保证演示可用）
    kafka_fallback_direct: bool = True
    kafka_client_timeout_ms: int = 8000

    # ---------------- 解析 ----------------
    #: auto | mineru | lite
    parser_backend: str = "auto"
    mineru_command: str = "mineru"
    #: 经典 MinerU(2.x/3.x) 命令模板；占位符 {input} {output} {backend} {lang} {method}
    mineru_cmd_template: str = (
        "{mineru} -p {input} -o {output} -b {backend} -m {method} -l {lang}"
    )
    mineru_backend: str = "pipeline"
    mineru_method: str = "auto"
    mineru_lang: str = "ch"
    mineru_timeout: int = 1800
    mineru_api_url: str = ""
    mineru_api_token: str = ""
    #: MinerU 抽出来的图片是否再转存一份（统一放在文档目录 images/ 下）
    keep_original_images: bool = True

    # ---------------- 切块 ----------------
    chunk_size: int = 700
    chunk_overlap: int = 120
    figure_context_chars: int = 260
    page_image_dpi: int = 120
    figure_image_dpi: int = 170

    # ---------------- 检索 ----------------
    rag_top_k: int = 6
    rag_figure_top_k: int = 3
    rag_max_context_chars: int = 9000
    rag_max_images: int = 4
    #: 是否把命中的图片/页面截图一起送给多模态模型（关闭可省 token）
    rag_attach_figures: bool = True
    rag_attach_pages: bool = False
    #: 图问时是否先让视觉模型生成一句图片描述用于检索
    rag_caption_query: bool = True
    #: 送入模型前图片的最长边
    rag_image_max_side: int = 1100
    #: bm25 | hybrid
    retrieval_mode: str = "bm25"
    embedding_model: str = ""
    embedding_device: str = "cpu"
    multimodal_embedding_model: str = ""

    # ---------------- 大模型 ----------------
    llm_provider: str = "qwen"
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_vision_model: str = ""
    llm_timeout: int = 120
    llm_max_tokens: int = 2048
    llm_temperature: float = 0.2
    llm_extra_body: str = ""
    #: 离线演示模式：不调用真实模型，用抽取式规则拼装答案（便于本地验证链路）
    llm_mock: bool = False

    # ---------------- 进程编排 ----------------
    embed_worker: bool = True
    worker_poll_ms: int = 500

    # ---------------- 派生字段（运行期填充） ----------------
    provider_label: str = field(default="", init=False)
    vision_enabled: bool = field(default=True, init=False)

    # ------------------------------------------------------------------ #
    @classmethod
    def load(cls) -> Settings:
        provider = _str("LLM_PROVIDER", "qwen").lower()
        preset = PROVIDER_PRESETS.get(provider, PROVIDER_PRESETS["custom"])
        data_dir = _path("DATA_DIR", BASE_DIR / "data")

        api_key = _str("LLM_API_KEY") or _preset_key(preset)
        base_url = _str("LLM_BASE_URL") or preset.get("base_url", "")
        model = _str("LLM_MODEL") or preset.get("model", "")
        vision_model = _str("LLM_VISION_MODEL") or preset.get("vision_model", "") or model

        allowed = tuple(
            ext if ext.startswith(".") else f".{ext}"
            for ext in (_str("ALLOWED_EXT", "pdf").lower().split(","))
            if ext.strip()
        )

        settings = cls(
            host=_str("HOST", "0.0.0.0"),  # noqa: S104
            port=_int("PORT", 8000),
            open_browser=_bool("OPEN_BROWSER", False),
            debug=_bool("DEBUG", False),
            data_dir=data_dir,
            static_dir=_path("STATIC_DIR", BASE_DIR / "static"),
            max_upload_mb=_int("MAX_UPLOAD_MB", 60),
            allowed_ext=allowed or (".pdf",),
            kafka_enabled=_bool("KAFKA_ENABLED", True),
            kafka_bootstrap=_str("KAFKA_BOOTSTRAP", "172.30.0.4:9094"),
            kafka_parse_topic=_str("KAFKA_PARSE_TOPIC", "rag.doc.parse"),
            kafka_status_topic=_str("KAFKA_STATUS_TOPIC", "rag.doc.status"),
            kafka_group=_str("KAFKA_GROUP", "rag-parser"),
            kafka_partitions=_int("KAFKA_PARTITIONS", 3),
            kafka_replicas=_int("KAFKA_REPLICAS", 1),
            kafka_auto_create=_bool("KAFKA_AUTO_CREATE", True),
            kafka_fallback_direct=_bool("KAFKA_FALLBACK_DIRECT", True),
            kafka_client_timeout_ms=_int("KAFKA_CLIENT_TIMEOUT_MS", 8000),
            parser_backend=_str("PARSER_BACKEND", "auto").lower(),
            mineru_command=_str("MINERU_COMMAND", "mineru"),
            mineru_cmd_template=_str(
                "MINERU_CMD_TEMPLATE",
                "{mineru} -p {input} -o {output} -b {backend} -m {method} -l {lang}",
            ),
            mineru_backend=_str("MINERU_BACKEND", "pipeline"),
            mineru_method=_str("MINERU_METHOD", "auto"),
            mineru_lang=_str("MINERU_LANG", "ch"),
            mineru_timeout=_int("MINERU_TIMEOUT", 1800),
            mineru_api_url=_str("MINERU_API_URL"),
            mineru_api_token=_str("MINERU_API_TOKEN"),
            keep_original_images=_bool("KEEP_ORIGINAL_IMAGES", True),
            chunk_size=_int("CHUNK_SIZE", 700),
            chunk_overlap=_int("CHUNK_OVERLAP", 120),
            figure_context_chars=_int("FIGURE_CONTEXT_CHARS", 260),
            page_image_dpi=_int("PAGE_IMAGE_DPI", 120),
            figure_image_dpi=_int("FIGURE_IMAGE_DPI", 170),
            rag_top_k=_int("RAG_TOP_K", 6),
            rag_figure_top_k=_int("RAG_FIGURE_TOP_K", 3),
            rag_max_context_chars=_int("RAG_MAX_CONTEXT_CHARS", 9000),
            rag_max_images=_int("RAG_MAX_IMAGES", 4),
            rag_attach_figures=_bool("RAG_ATTACH_FIGURES", True),
            rag_attach_pages=_bool("RAG_ATTACH_PAGES", False),
            rag_caption_query=_bool("RAG_CAPTION_QUERY", True),
            rag_image_max_side=_int("RAG_IMAGE_MAX_SIDE", 1100),
            retrieval_mode=_str("RETRIEVAL_MODE", "bm25").lower(),
            embedding_model=_str("EMBEDDING_MODEL"),
            embedding_device=_str("EMBEDDING_DEVICE", "cpu"),
            multimodal_embedding_model=_str("MULTIMODAL_EMBEDDING_MODEL"),
            llm_provider=provider,
            llm_base_url=base_url,
            llm_api_key=api_key,
            llm_model=model,
            llm_vision_model=vision_model,
            llm_timeout=_int("LLM_TIMEOUT", 120),
            llm_max_tokens=_int("LLM_MAX_TOKENS", 2048),
            llm_temperature=_float("LLM_TEMPERATURE", 0.2),
            llm_extra_body=_str("LLM_EXTRA_BODY"),
            llm_mock=_bool("LLM_MOCK", False),
            embed_worker=_bool("EMBED_WORKER", True),
            worker_poll_ms=_int("WORKER_POLL_MS", 500),
        )
        settings.provider_label = preset.get("label", "自定义服务")
        settings.vision_enabled = bool(vision_model)
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        (settings.data_dir / "documents").mkdir(parents=True, exist_ok=True)
        return settings

    # ------------------------------------------------------------------ #
    # 派生路径
    # ------------------------------------------------------------------ #
    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def documents_dir(self) -> Path:
        return self.data_dir / "documents"

    @property
    def llm_ready(self) -> bool:
        return self.llm_mock or bool(self.llm_api_key and self.llm_model and self.llm_base_url)

    def doc_dir(self, doc_id: str) -> Path:
        return self.documents_dir / doc_id

    def ensure_doc_dir(self, doc_id: str) -> Path:
        path = self.doc_dir(doc_id)
        (path / "images").mkdir(parents=True, exist_ok=True)
        (path / "pages").mkdir(parents=True, exist_ok=True)
        return path

    def public_dict(self) -> dict[str, Any]:
        """可以安全下发给前端的配置（不含任何密钥）。"""
        return {
            "provider": self.llm_provider,
            "provider_label": self.provider_label,
            "model": self.llm_model,
            "vision_model": self.llm_vision_model,
            "vision_enabled": self.vision_enabled,
            "llm_ready": self.llm_ready,
            "mock_mode": self.llm_mock,
            "parser_backend": self.parser_backend,
            "retrieval_mode": self.retrieval_mode,
            "kafka": {
                "enabled": self.kafka_enabled,
                "bootstrap": self.kafka_bootstrap,
                "parse_topic": self.kafka_parse_topic,
                "status_topic": self.kafka_status_topic,
                "group": self.kafka_group,
            },
            "limits": {
                "max_upload_mb": self.max_upload_mb,
                "allowed_ext": list(self.allowed_ext),
                "top_k": self.rag_top_k,
                "figure_top_k": self.rag_figure_top_k,
            },
        }


settings = Settings.load()
