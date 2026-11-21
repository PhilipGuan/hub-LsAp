import asyncio

from backend.agents.responder.responder import Responder
from backend.common.llm import create_model
from backend.model.schemas import Classification
from backend.common.settings import Settings, configure_logging
from backend.common.kafka_utils import run_worker


async def main():
    settings = Settings()
    configure_logging(settings)
    await run_worker(settings, input_topic=settings.kafka_classification_topic,
                     group=settings.kafka_responder_group,
                     output_topic=settings.kafka_response_topic,
                     schema=Classification, process=Responder(create_model(settings)), stage="response")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
