"""FastAPI 与 Kafka 之间的桥接层。

职责：
- 生产 raw_input 消息；
- 后台消费 sentiment_result，按 request_id 聚合 type=sentiment / type=reply；
- ask() 阻塞等待最终回复，超时抛 RequestTimeoutError。
"""

from __future__ import annotations

import asyncio
import logging
import sys
import uuid
from dataclasses import dataclass

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.errors import KafkaError
from pydantic import ValidationError

from shared.config import KafkaSettings, load_kafka_settings
from shared.kafka_config import create_consumer, create_producer, ensure_topics
from shared.schemas import (
    ChatResponse,
    RawInputMessage,
    ReplyMessage,
    Sentiment,
    SentimentMessage,
)

logger = logging.getLogger(__name__)


class KafkaBridgeError(RuntimeError):
    """Kafka 连接或消息处理失败。"""


class RequestTimeoutError(KafkaBridgeError):
    """等待 sentiment_result 超时。"""


@dataclass
class _PendingRequest:
    text: str
    future: asyncio.Future[ChatResponse]
    sentiment: Sentiment | None = None


class KafkaBridge:
    def __init__(self, settings: KafkaSettings | None = None) -> None:
        self.settings = settings or load_kafka_settings()
        self._producer: AIOKafkaProducer | None = None
        self._consumer: AIOKafkaConsumer | None = None
        self._consume_task: asyncio.Task[None] | None = None
        self._pending: dict[str, _PendingRequest] = {}

    async def start(self) -> None:
        if self._producer is not None:
            return
        try:
            await ensure_topics(self.settings)
            self._producer = create_producer(self.settings)
            await self._producer.start()
            self._consumer = create_consumer(
                [self.settings.sentiment_result_topic],
                self.settings.backend_group_id,
                self.settings,
            )
            await self._consumer.start()
        except Exception as exc:
            await self.stop()
            raise KafkaBridgeError(f"Kafka 连接失败: {exc}") from exc
        self._consume_task = asyncio.create_task(self._consume_loop(), name="kafka-bridge-consumer")

    async def stop(self) -> None:
        if self._consume_task is not None:
            self._consume_task.cancel()
            try:
                await self._consume_task
            except asyncio.CancelledError:
                pass
            self._consume_task = None
        self._fail_all(KafkaBridgeError("Kafka bridge 已停止"))
        if self._consumer is not None:
            await self._consumer.stop()
            self._consumer = None
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None

    async def ask(self, text: str, timeout: float | None = None) -> ChatResponse:
        """生产一条用户输入并等待最终回复。"""
        if self._producer is None:
            raise KafkaBridgeError("Kafka bridge 未启动，请先调用 start()")

        request_id = uuid.uuid4().hex
        pending = _PendingRequest(text=text, future=asyncio.get_running_loop().create_future())
        self._pending[request_id] = pending
        timeout = self.settings.request_timeout_seconds if timeout is None else timeout

        message = RawInputMessage(request_id=request_id, text=text)
        try:
            await self._producer.send_and_wait(
                self.settings.raw_input_topic,
                key=request_id,
                value=message.model_dump(mode="json"),
            )
            return await asyncio.wait_for(pending.future, timeout)
        except asyncio.TimeoutError as exc:
            raise RequestTimeoutError(f"请求 {request_id} 等待回复超时（{timeout}s）") from exc
        except KafkaError as exc:
            raise KafkaBridgeError(f"生产 raw_input 失败: {exc}") from exc
        finally:
            self._pending.pop(request_id, None)

    async def _consume_loop(self) -> None:
        assert self._consumer is not None
        try:
            async for record in self._consumer:
                try:
                    self._handle(record.value)
                except (ValidationError, AttributeError) as exc:
                    logger.warning("忽略非法消息 %r: %s", record.value, exc)
                except Exception:
                    logger.exception("处理 sentiment_result 消息失败")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Kafka 消费循环退出")
            self._fail_all(KafkaBridgeError(f"Kafka 消费中断: {exc}"))

    def _handle(self, payload: dict) -> None:
        message_type = payload.get("type")
        if message_type == "sentiment":
            self._on_sentiment(SentimentMessage.model_validate(payload))
        elif message_type == "reply":
            self._on_reply(ReplyMessage.model_validate(payload))

    def _on_sentiment(self, message: SentimentMessage) -> None:
        pending = self._pending.get(message.request_id)
        if pending is not None:
            pending.sentiment = message.sentiment

    def _on_reply(self, message: ReplyMessage) -> None:
        pending = self._pending.get(message.request_id)
        if pending is None or pending.future.done():
            return
        pending.future.set_result(
            ChatResponse(
                request_id=message.request_id,
                input=pending.text,
                sentiment=pending.sentiment or message.sentiment,
                reply=message.reply,
            )
        )

    def _fail_all(self, error: Exception) -> None:
        for pending in self._pending.values():
            if not pending.future.done():
                pending.future.set_exception(error)
        self._pending.clear()


async def _fake_worker(settings: KafkaSettings) -> None:
    """自测用假 worker：收到 raw_input 后回写 sentiment + reply。"""
    consumer = create_consumer(
        [settings.raw_input_topic],
        "kafka-bridge-selftest-worker",
        settings,
        auto_offset_reset="latest",
    )
    producer = create_producer(settings)
    await consumer.start()
    await producer.start()
    try:
        async for record in consumer:
            raw = record.value
            messages = [
                SentimentMessage(request_id=raw["request_id"], text=raw["text"], sentiment="negative"),
                ReplyMessage(request_id=raw["request_id"], sentiment="negative", reply="这是自测回复"),
            ]
            for message in messages:
                await producer.send_and_wait(
                    settings.sentiment_result_topic,
                    key=raw["request_id"],
                    value=message.model_dump(mode="json"),
                )
            break
    finally:
        await producer.stop()
        await consumer.stop()


async def main() -> None:
    """自测：启动 bridge + 假 worker，走完一次 ask()。"""
    settings = load_kafka_settings()
    bridge = KafkaBridge(settings)
    await bridge.start()
    worker = asyncio.create_task(_fake_worker(settings))
    await asyncio.sleep(1)  # 等假 worker 加入消费组
    try:
        response = await bridge.ask("今天工作好累", timeout=20)
        print(response.model_dump_json(indent=2))
    finally:
        worker.cancel()
        await bridge.stop()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
