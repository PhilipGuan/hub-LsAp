#!/usr/bin/env python
"""Create the two demo topics if they do not exist yet."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.kafka_bus import ensure_topics


if __name__ == "__main__":
    asyncio.run(ensure_topics())
    print("Kafka topics are ready")
