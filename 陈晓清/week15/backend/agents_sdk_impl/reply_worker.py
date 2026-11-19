"""进程 2：消费 sentiment_result 中 type=sentiment，生成回复，生产 type=reply。

运行：python -m backend.agents_sdk_impl.reply_worker
"""

from __future__ import annotations

import asyncio
import logging
import sys

from backend.agents_sdk_impl.agents.reply_agent import generate_reply
from shared.config import load_kafka_settings
from shared.kafka_config import create_consumer, create_producer
from shared.schemas import ReplyMessage, SentimentMessage

logger = logging.getLogger(__name__)


async def main() -> None:
    settings = load_kafka_settings()
    consumer = create_consumer(
        [settings.sentiment_result_topic],
        settings.reply_worker_group_id,
        settings,
        auto_offset_reset="latest",
        enable_auto_commit=False,
    )
    producer = create_producer(settings)
    await consumer.start()
    await producer.start()
    print(f"reply worker 已启动: 消费 {settings.sentiment_result_topic} 中 type=sentiment", flush=True)
    try:
        async for record in consumer:
            try:
                if record.value.get("type") != "sentiment":
                    await consumer.commit()
                    continue
                incoming = SentimentMessage.model_validate(record.value)
                reply = await generate_reply(incoming.text, incoming.sentiment)
                message = ReplyMessage(
                    request_id=incoming.request_id,
                    sentiment=incoming.sentiment,
                    reply=reply,
                )
                await producer.send_and_wait(
                    settings.sentiment_result_topic,
                    key=incoming.request_id,
                    value=message.model_dump(mode="json"),
                )
                print(f"[reply] {incoming.request_id} -> {incoming.sentiment}", flush=True)
            except Exception:
                logger.exception("处理 sentiment 消息失败: %r", record.value)
            await consumer.commit()
    finally:
        await producer.stop()
        await consumer.stop()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s | %(message)s")
    asyncio.run(main())
