import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from backend.app.routes import ResultDispatcher, router
from backend.common.kafka_utils import kafka_clients
from backend.common.settings import Settings, configure_logging

logger = logging.getLogger(__name__)


def create_app(settings=None, client_factory=kafka_clients):
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        configure_logging(settings)
        logger.info("FastAPI 后端启动，正在初始化 Kafka 结果消费者")
        dispatcher = ResultDispatcher()
        async with client_factory(settings, settings.kafka_response_topic, settings.kafka_api_group) as (producer, consumer):
            task = asyncio.create_task(dispatcher.consume(consumer))
            def consumer_done(finished):
                if not finished.cancelled() and finished.exception() is not None:
                    logger.error("Kafka 结果消费者异常退出 error=%s", type(finished.exception()).__name__)
            task.add_done_callback(consumer_done)
            app.state.producer = producer
            app.state.dispatcher = dispatcher
            app.state.consumer_task = task
            logger.info("FastAPI 后端就绪，可接收聊天请求")
            try:
                yield
            finally:
                logger.info("FastAPI 后端关闭中 pending_requests=%s", len(dispatcher.pending))
                task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await task
                for request_id in list(dispatcher.pending):
                    dispatcher.discard(request_id)
        logger.info("FastAPI 后端已关闭")

    app = FastAPI(title="情感分析聊天系统", lifespan=lifespan)

    app.state.settings = settings
    app.include_router(router)
    return app


app = create_app()
