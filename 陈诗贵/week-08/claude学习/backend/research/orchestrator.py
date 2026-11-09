"""研究编排层：串起 engine（循环）+ storage（落盘），产出一次完整研究。"""
from __future__ import annotations

import logging

from ..engine import loop
from ..models import ResearchRecord
from ..storage import writer

logger = logging.getLogger(__name__)


async def research(topic: str, record_id: str | None = None) -> ResearchRecord:
    """对主题执行深度研究并落盘，返回 ResearchRecord。"""
    logger.info("开始深度研究：%s", topic)
    record = await loop.run(topic, record_id=record_id)
    d = writer.write(record)
    logger.info("研究完成，产物已写入：%s", d)
    return record
