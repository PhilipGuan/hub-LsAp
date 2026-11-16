"""环境自检脚本。

    python scripts/check_env.py

输出 Kafamen、解析后端、大模型、检索索引、目录等关键状态，便于排查演示环境。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app import db, kafka_bus  # noqa: E402
from app.config import settings  # noqa: E402
from app.logging_setup import setup_logging  # noqa: E402
from app.parsing import backend_status, mineru_backend  # noqa: E402
from app.rag import index, llm  # noqa: E402


def main() -> int:
    setup_logging()
    db.init_db()

    kafka_ok, kafka_message = kafka_bus.kafka_health()
    mineru_ok, mineru_message = mineru_backend.probe(force=True)
    parser = backend_status()

    index.ensure_fresh()
    stats = index.stats()

    report = {
        "python": sys.version.split()[0],
        "data_dir": str(settings.data_dir),
        "kafka": {
            "enabled": settings.kafka_enabled,
            "bootstrap": settings.kafka_bootstrap,
            "ok": kafka_ok,
            "message": kafka_message,
            "topics": [settings.kafka_parse_topic, settings.kafka_status_topic],
        },
        "parser": {
            "configured": settings.parser_backend,
            "active": parser["active"],
            "mineru_ok": mineru_ok,
            "mineru_detail": mineru_message,
            "mineru_command": settings.mineru_command,
        },
        "llm": {
            **llm.status(),
            "base_url": settings.llm_base_url,
        },
        "retrieval": {
            "mode": settings.retrieval_mode,
            "embedding_model": settings.embedding_model,
            "multimodal_embedding_model": settings.multimodal_embedding_model,
            **stats,
        },
        "documents": db.document_counts(),
        "platforms": {
            "jieba": _has_module("jieba"),
            "pdfplumber": _has_module("pdfplumber"),
            "pypdfium2": _has_module("pypdfium2"),
            "kafka": _has_module("kafka"),
            "markdown_it": _has_module("markdown_it"),
            "sentence_transformers": _has_module("sentence_transformers"),
        },
    }

    print(json.dumps(report, ensure_ascii=False, indent=2))

    problems: list[str] = []
    if settings.kafka_enabled and not kafka_ok:
        problems.append(f"Kafka 不可用：{kafka_message}")
    if not settings.llm_ready:
        problems.append("大模型未配置：在 .env 填 LLM_API_KEY，或设 LLM_MOCK=1")
    if not report["platforms"]["pdfplumber"] or not report["platforms"]["pypdfium2"]:
        problems.append("内置解析器依赖缺失：pip install -r requirements.txt")

    if problems:
        print("\n需要处理：")
        for item in problems:
            print(f"  - {item}")
        return 1
    print("\n环境检查通过。")
    return 0


def _has_module(name: str) -> bool:
    try:
        __import__(name)
        return True
    except ModuleNotFoundError:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
