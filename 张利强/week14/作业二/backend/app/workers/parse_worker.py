"""PDF 解析 worker：消费 Kafka pdf-parse，调用 MinerU 解析并写 ES。"""

import logging
import signal
import time
from datetime import datetime, timezone

from ..config import get_settings
from ..db import SessionLocal, init_db
from ..kafka_client import make_consumer, publish_dlq, publish_status
from ..models import Document
from ..services.pipeline import process_document

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [worker] %(message)s",
)
log = logging.getLogger(__name__)

settings = get_settings()
_running = True


def _stop(*_args) -> None:
    global _running
    _running = False


def _status(doc: Document, status: str, **extra) -> None:
    payload = {
        "type": "doc_status",
        "doc_id": doc.id,
        "filename": doc.filename,
        "status": status,
        "error": doc.error,
        "chunk_count": doc.chunk_count,
        "page_count": doc.page_count,
    }
    payload.update(extra)
    publish_status(payload)


def handle(payload: dict) -> None:
    doc_id = payload.get("doc_id") if isinstance(payload, dict) else None
    if not doc_id:
        log.warning("skip malformed message: %s", payload)
        return
    db = SessionLocal()
    try:
        doc = db.get(Document, doc_id)
        if doc is None:
            log.warning("document %s not found, skip", doc_id)
            return
        if doc.status == "completed":
            log.info("document %s already completed, skip", doc_id)
            return

        doc.status = "parsing"
        doc.attempts = (doc.attempts or 0) + 1
        doc.error = None
        db.commit()
        _status(doc, "parsing")

        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                log.info("parse %s attempt %s", doc_id, attempt)
                result = process_document(doc.id, doc.file_path, doc.filename)
                doc.status = "completed"
                doc.page_count = result["page_count"]
                doc.chunk_count = result["chunk_count"]
                doc.parsed_at = datetime.now(timezone.utc)
                last_error = None
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                log.exception("parse %s attempt %s failed", doc_id, attempt)
                doc.error = str(exc)[:1000]
                db.commit()
                if attempt < 3:
                    time.sleep(2 * attempt)

        if last_error is not None:
            doc.status = "failed"
            publish_dlq(
                {
                    "doc_id": doc.id,
                    "filename": doc.filename,
                    "file_path": doc.file_path,
                    "error": doc.error,
                }
            )

        db.commit()
        _status(doc, doc.status)
        log.info("document %s -> %s", doc.id, doc.status)
    finally:
        db.close()


def main() -> None:
    init_db()
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    consumer = make_consumer(settings.kafka_topic_parse, "pdf-parse-workers")
    log.info("worker started, topic=%s", settings.kafka_topic_parse)
    try:
        while _running:
            batches = consumer.poll(timeout_ms=1000)
            for _tp, messages in batches.items():
                for message in messages:
                    if not _running:
                        break
                    try:
                        handle(message.value)
                    except Exception:  # noqa: BLE001
                        log.exception("handle message failed: %s", message.value)
                    consumer.commit()
    finally:
        consumer.close()
        log.info("worker stopped")


if __name__ == "__main__":
    main()
