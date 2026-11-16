"""初始化 Kafka Topic（幂等）：pdf-parse-task。

用法（任意目录）：python scripts/init_kafka_topics.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from kafka.admin import KafkaAdminClient, NewTopic  # noqa: E402

from app.core.config import get_settings  # noqa: E402


def main() -> None:
    settings = get_settings()
    topic_name = settings.kafka_topic_parse_task
    admin = KafkaAdminClient(bootstrap_servers=settings.kafka_bootstrap_servers)
    try:
        existing = admin.list_topics()
        if topic_name in existing:
            print(f"Topic 已存在，跳过: {topic_name}")
        else:
            admin.create_topics([
                NewTopic(name=topic_name, num_partitions=3, replication_factor=1)
            ])
            print(f"Topic 创建成功: {topic_name} (partitions=3)")
    finally:
        admin.close()


if __name__ == "__main__":
    main()
