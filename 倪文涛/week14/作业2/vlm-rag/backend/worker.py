import json
import logging
import signal
from fcntl import LOCK_EX, LOCK_NB, flock
from uuid import UUID

from confluent_kafka import Consumer, KafkaException

from backend.config import load_config
from backend.logging_setup import configure_logging, error_context
from backend.models.document import Task
from backend.service.documents import Documents
from backend.service.index import Index
from backend.service.ingest import process
from backend.service.model_clients import Embedding, Vision


logger = logging.getLogger(__name__)


def consume(config: dict) -> None:
    index = Index(config)
    documents = Documents(config, index)
    embedding, vision = Embedding(config), Vision(config)
    kafka = config["kafka"]
    consumer = Consumer({
        **kafka["options"],
        "group.id": kafka["group_id"],
        "auto.offset.reset": "earliest",
        "enable.auto.commit": False,
        "max.poll.interval.ms": 86_400_000,
    })
    running = True

    def stop(*_):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        documents.recover_queued()
        consumer.subscribe([kafka["topic"]])
        logger.info("worker_started topic=%s group=%s", kafka["topic"], kafka["group_id"])
        while running:
            message = consumer.poll(1)
            if message is None:
                continue
            error = message.error()
            if error is not None:
                if error.fatal():
                    raise KafkaException(error)
                logger.warning("kafka_poll_error code=%s", error.code())
                continue
            try:
                task = Task(**json.loads(message.value()))
                UUID(task.document_id)
                UUID(task.attempt_id)
            except (ValueError, TypeError, AttributeError):
                logger.warning("invalid_kafka_task partition=%d offset=%d",
                               message.partition(), message.offset())
            else:
                process(task, config, documents, index, embedding, vision)
            # 任务状态写入 Redis 后才提交位点。中途退出时，Kafka 可以重新投递。
            consumer.commit(message=message, asynchronous=False)
    finally:
        consumer.close()
        embedding.client.close()
        vision.client.close()
        index.client.close()
        documents.redis.close()
        logger.info("worker_stopped")


def main() -> None:
    configure_logging()
    config = load_config()
    data_dir = config["data_dir"]
    data_dir.mkdir(parents=True, exist_ok=True)
    with (data_dir / ".worker.lock").open("a") as lock:
        try:
            flock(lock, LOCK_EX | LOCK_NB)
        except BlockingIOError:
            raise SystemExit("A Worker is already running for this data directory.") from None
        try:
            consume(config)
        except Exception as exc:
            logger.error("worker_failed error=%s", error_context(exc))
            raise SystemExit(1) from None


if __name__ == "__main__":
    main()
