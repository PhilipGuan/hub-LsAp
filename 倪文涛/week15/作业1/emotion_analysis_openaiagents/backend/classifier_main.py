import asyncio

from backend.agents.classifier.classifier import Classifier
from backend.common.llm import create_model
from backend.model.schemas import UserInput
from backend.common.settings import Settings, configure_logging
from backend.common.kafka_utils import run_worker


async def main():
    settings = Settings()
    configure_logging(settings)
    await run_worker(settings, input_topic=settings.kafka_request_topic,
                     group=settings.kafka_classifier_group,
                     output_topic=settings.kafka_classification_topic,
                     schema=UserInput, process=Classifier(create_model(settings)), stage="classification")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
