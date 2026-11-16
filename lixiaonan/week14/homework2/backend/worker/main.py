"""解析 Worker 入口（独立进程）。

启动：cd backend && python -m worker.main
"""
from app.core.config import get_settings
from app.core.logging import logger, setup_logging
from app.db.base import SessionLocal, init_db
from app.mq.kafka_producer import MQProducer
from app.services.storage_service import build_storage
from worker.consumer import ParseWorker
from worker.parser import get_parser


def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level, settings.logs_path)
    settings.ensure_dirs()
    init_db()  # 幂等建表，Worker 可独立于 API 先启动

    storage = build_storage(settings)
    parser = get_parser(settings)
    mq = MQProducer(settings)  # 仅用于失败重试时重新入队
    worker = ParseWorker(settings, storage, parser, SessionLocal, mq)

    logger.info(
        f"Worker 启动 topic={settings.kafka_topic_parse_task} "
        f"group={settings.kafka_consumer_group} parser={parser.name} "
        f"storage={settings.storage_backend} max_retry={settings.kafka_max_retry}"
    )
    worker.run()


if __name__ == "__main__":
    main()
