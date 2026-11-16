import logging
import re

from confluent_kafka import Consumer, KafkaException, Producer

from .ingest import process_job


class UploadProducer:
    def __init__(self, config, topic, max_message_bytes):
        self.topic = topic
        self.client = Producer({**config, "enable.idempotence": True,
                                "message.max.bytes": max_message_bytes, "message.timeout.ms": 12000})

    def send(self, doc_id, name, data):
        delivery = []
        try:
            self.client.produce(self.topic, key=doc_id.encode("ascii"), value=data,
                                headers={"filename": name.encode("utf-8")},
                                on_delivery=lambda error, _: delivery.append(error))
            self.client.flush(15)
        except (KafkaException, BufferError) as exc:
            raise RuntimeError("Kafka rejected upload") from exc
        if not delivery or delivery[0] is not None:
            raise RuntimeError(f"Kafka did not acknowledge upload: {delivery[0] if delivery else 'timeout'}")


def consume(config, topic, group, fetch_bytes, timeout, store, parser):
    consumer = Consumer({
        **config, "group.id": group, "auto.offset.reset": "earliest",
        "enable.auto.commit": False, "max.poll.interval.ms": (timeout + 180) * 1000,
        "max.partition.fetch.bytes": fetch_bytes, "fetch.max.bytes": fetch_bytes * 2,
    })
    consumer.subscribe([topic])
    logging.info("Waiting for PDF records on %s", topic)
    try:
        while True:
            message = consumer.poll(1)
            if message is None:
                continue
            if message.error():
                logging.warning("Kafka consumer: %s", message.error())
                if message.error().fatal():
                    raise KafkaException(message.error())
                continue
            try:
                doc_id = message.key().decode("ascii")
                name = dict(message.headers() or [])["filename"].decode("utf-8")
                if not re.fullmatch(r"[0-9a-f]{32}", doc_id) or not message.value():
                    raise ValueError("invalid document ID or empty PDF")
            except (AttributeError, KeyError, ValueError):
                logging.warning("Skipping a record without a valid PDF envelope at offset %s", message.offset())
            else:
                process_job(store, parser, doc_id, name, message.value())
                logging.info("Stored result for document %s", doc_id)
            committed = consumer.commit(message=message, asynchronous=False)
            for partition in committed:
                if partition.error is not None:
                    raise KafkaException(partition.error)
    finally:
        consumer.close()
