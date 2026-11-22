# -*- coding: utf-8 -*-
"""worker 进程：Kafka 消费 / 数据库轮询 / 自动降级。"""

from __future__ import annotations

import argparse
import logging
import time

from ..core.config import KAFKA_TOPIC, STATUS_PENDING
from ..core.db import init_db, session_scope
from ..orm import File
from ..services import kafka_bus
from .tasks import process_file

logger = logging.getLogger("rag.worker")


def handle_message(message: dict) -> None:
    file_id = message.get("file_id")
    if file_id is None:
        logger.warning("收到缺少 file_id 的消息：%s", message)
        return
    logger.info("开始处理 file_id=%s (%s)", file_id, message.get("file_name"))
    ok = process_file(int(file_id))
    logger.info("处理结束 file_id=%s ok=%s", file_id, ok)


def run_kafka() -> int:
    logger.info("Kafka 模式：topic=%s bootstrap=localhost:9092", KAFKA_TOPIC)
    kafka_bus.consume(handle_message)
    return 0


def _next_pending_id() -> int | None:
    with session_scope() as session:
        row = (
            session.query(File)
            .filter(File.status == STATUS_PENDING)
            .order_by(File.id.asc())
            .first()
        )
        return int(row.id) if row else None


def run_poll(once: bool = False) -> int:
    logger.info("轮询模式：每 5 秒扫描一次待解析文件")
    while True:
        file_id = _next_pending_id()
        if file_id is None:
            if once:
                return 0
            time.sleep(5)
            continue

        logger.info("发现待解析文件 file_id=%s", file_id)
        process_file(file_id)
        if once:
            return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="多模态 RAG 解析 worker")
    parser.add_argument("--mode", choices=["auto", "kafka", "poll"], default="auto")
    parser.add_argument("--once", action="store_true", help="处理一轮后退出")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    init_db()

    if args.mode == "kafka":
        return run_kafka()
    if args.mode == "poll":
        return run_poll(once=args.once)

    if kafka_bus.broker_available():
        try:
            return run_kafka()
        except Exception as exc:
            logger.warning("Kafka 消费失败，切换到轮询模式：%s", exc)
    else:
        logger.warning("Kafka 不可用，使用轮询模式")
    return run_poll(once=args.once)


if __name__ == "__main__":
    raise SystemExit(main())
