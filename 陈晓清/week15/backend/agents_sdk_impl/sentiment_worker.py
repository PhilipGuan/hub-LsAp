"""进程 1：消费 raw_input，调用情感识别 Agent，生产 type=sentiment。

运行：python -m backend.agents_sdk_impl.sentiment_worker
"""

from __future__ import annotations

import asyncio
import logging
import sys

from backend.agents_sdk_impl.agents.sentiment_agent import classify
from shared.config import load_kafka_settings
from shared.kafka_config import create_consumer, create_producer
from shared.schemas import RawInputMessage, SentimentMessage

logger = logging.getLogger(__name__)


async def main() -> None:
    settings = load_kafka_settings()
    consumer = create_consumer(
        [settings.raw_input_topic],
        settings.sentiment_worker_group_id,
        settings,
        auto_offset_reset="latest",
        enable_auto_commit=False,
    )
    producer = create_producer(settings)
    await consumer.start()
    await producer.start()
    print(f"sentiment worker 已启动: {settings.raw_input_topic} -> {settings.sentiment_result_topic}", flush=True)
    try:
        async for record in consumer:
            try:
                raw = RawInputMessage.model_validate(record.value)
                sentiment = await classify(raw.text)
                message = SentimentMessage(request_id=raw.request_id, text=raw.text, sentiment=sentiment)
                await producer.send_and_wait(
                    settings.sentiment_result_topic,
                    key=raw.request_id,
                    value=message.model_dump(mode="json"),
                )
                print(f"[sentiment] {raw.request_id} -> {sentiment}", flush=True)
            except Exception:
                logger.exception("处理 raw_input 失败: %r", record.value)
            await consumer.commit()
    finally:
        await producer.stop()
        await consumer.stop()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s | %(message)s")
    asyncio.run(main())
