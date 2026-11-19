"""进程 1（LangGraph 版）：消费 raw_input，情感识别，生产 type=sentiment。

数据流：raw_input -> 情感图 classify_sentiment() -> sentiment_result(type=sentiment)

运行：python -m backend.langgraph_impl.sentiment_worker
"""

from __future__ import annotations

import asyncio
import logging
import sys

from backend.langgraph_impl.graphs.sentiment_graph import classify_sentiment
from shared.config import load_kafka_settings
from shared.kafka_config import create_consumer, create_producer
from shared.schemas import RawInputMessage, SentimentMessage

logger = logging.getLogger(__name__)


async def main() -> None:
    settings = load_kafka_settings()

    # 只订阅 raw_input；latest 表示新进程只处理启动后的新消息，不回放历史
    consumer = create_consumer(
        [settings.raw_input_topic],
        settings.sentiment_worker_group_id,
        settings,
        auto_offset_reset="latest",
        enable_auto_commit=False,  # 关闭自动提交，处理完一条再手动 commit
    )
    producer = create_producer(settings)
    await consumer.start()
    await producer.start()
    print(f"sentiment worker (LangGraph) 已启动: {settings.raw_input_topic} -> {settings.sentiment_result_topic}", flush=True)

    try:
        # 长驻循环：拿到一条消费一条；停止进程用 Ctrl+C，由 finally 收尾
        async for record in consumer:
            try:
                # 反序列化并校验上游消息，结构不对会抛 ValidationError
                raw = RawInputMessage.model_validate(record.value)

                # 调用 LangGraph 图完成情感识别，返回 positive / negative
                sentiment = await classify_sentiment(raw.text)

                # 组装结果消息，保留 request_id 供后端聚合
                message = SentimentMessage(request_id=raw.request_id, text=raw.text, sentiment=sentiment)

                # key 用 request_id：同一请求的消息落在同一分区，保证后端先收到 sentiment 再收到 reply
                # model_dump(mode="json") 把 datetime 等类型转成可 JSON 序列化的值
                await producer.send_and_wait(
                    settings.sentiment_result_topic,
                    key=raw.request_id,
                    value=message.model_dump(mode="json"),
                )
                print(f"[sentiment] {raw.request_id} -> {sentiment}", flush=True)
            except Exception:
                # LLM/校验失败只记日志，不写非法消息；随后 commit，避免毒消息卡住分区
                logger.exception("处理 raw_input 失败: %r", record.value)

            # 无论成功与否都提交偏移量：失败的消息由后端按超时处理，不重复消费
            await consumer.commit()
    finally:
        # 先停生产者再停消费者，避免关闭过程中仍在发送
        await producer.stop()
        await consumer.stop()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    # 日志格式：时间 级别 模块 | 内容
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s | %(message)s")
    asyncio.run(main())
