"""进程 2（LangGraph 版）：消费 type=sentiment，生成回复，生产 type=reply。

数据流：sentiment_result(type=sentiment) -> 回复图 generate_reply() -> sentiment_result(type=reply)

运行：python -m backend.langgraph_impl.reply_worker
"""

from __future__ import annotations

import asyncio
import logging
import sys

from backend.langgraph_impl.graphs.reply_graph import generate_reply
from shared.config import load_kafka_settings
from shared.kafka_config import create_consumer, create_producer
from shared.schemas import ReplyMessage, SentimentMessage

logger = logging.getLogger(__name__)


async def main() -> None:
    settings = load_kafka_settings()

    # 与进程 1 订阅同一个 topic，但用不同 group id，因此两边都能收到全部消息
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
    print(f"reply worker (LangGraph) 已启动: 消费 {settings.sentiment_result_topic} 中 type=sentiment", flush=True)

    try:
        async for record in consumer:
            try:
                # sentiment_result 里混着 sentiment 和 reply 两种消息，本进程只处理前者
                if record.value.get("type") != "sentiment":
                    await consumer.commit()
                    continue

                incoming = SentimentMessage.model_validate(record.value)

                # 按情感选择鼓励 / 排解提示词，由回复图生成文本
                reply = await generate_reply(incoming.text, incoming.sentiment)

                # 保留 request_id 和 sentiment，后端靠 request_id 匹配请求
                message = ReplyMessage(
                    request_id=incoming.request_id,
                    sentiment=incoming.sentiment,
                    reply=reply,
                )
                # 同样写到 sentiment_result，用 type=reply 与上游消息区分
                await producer.send_and_wait(
                    settings.sentiment_result_topic,
                    key=incoming.request_id,
                    value=message.model_dump(mode="json"),
                )
                print(f"[reply] {incoming.request_id} -> {incoming.sentiment}", flush=True)
            except Exception:
                # LLM 失败或消息非法：记日志后 commit，不生产空回复或非法消息
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
