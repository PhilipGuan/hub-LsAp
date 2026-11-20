import logging
import os
import sys

from .broker import UploadProducer, consume
from .ingest import MinerU
from .qa import VisionChat
from .store import Store
from .web import create_app


def kafka_config():
    settings = {"bootstrap.servers": os.getenv("KAFKA_BOOTSTRAP_SERVERS", "127.0.0.1:9092")}
    for env, key in {
        "KAFKA_SECURITY_PROTOCOL": "security.protocol", "KAFKA_SASL_MECHANISM": "sasl.mechanisms",
        "KAFKA_SASL_USERNAME": "sasl.username", "KAFKA_SASL_PASSWORD": "sasl.password",
    }.items():
        if os.getenv(env):
            settings[key] = os.environ[env]
    return settings


def make_app():
    store = Store(os.getenv("RAG_DATABASE", "data/rag.sqlite3"))
    max_pdf_bytes = int(os.getenv("MAX_PDF_MIB", "10")) * 1024 * 1024
    producer = UploadProducer(kafka_config(), os.getenv("KAFKA_TOPIC", "rag.pdf.v1"), max_pdf_bytes + 256 * 1024)
    model, base_url = os.getenv("LLM_MODEL"), os.getenv("LLM_BASE_URL")
    chat = VisionChat(store, base_url, model, os.getenv("LLM_API_KEY", "")) if model and base_url else None
    return create_app(store, producer.send, chat, max_pdf_bytes)


def worker():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    store = Store(os.getenv("RAG_DATABASE", "data/rag.sqlite3"))
    timeout = int(os.getenv("MINERU_TIMEOUT_SECONDS", "600"))
    parser = MinerU(os.getenv("MINERU_URL", "http://127.0.0.1:8000/file_parse"),
                    os.getenv("MINERU_BACKEND", "pipeline"), os.getenv("MINERU_LANGUAGE", "ch"), timeout)
    consume(kafka_config(), os.getenv("KAFKA_TOPIC", "rag.pdf.v1"), os.getenv("KAFKA_GROUP", "rag-mineru-v1"),
            (int(os.getenv("MAX_PDF_MIB", "10")) + 2) * 1024 * 1024, timeout, store, parser.parse)


if __name__ == "__main__":
    if sys.argv[1:] != ["worker"]:
        raise SystemExit("Usage: python -m backend.service worker")
    worker()
