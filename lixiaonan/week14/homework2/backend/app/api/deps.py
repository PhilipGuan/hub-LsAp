"""FastAPI 依赖注入：数据库会话 / 存储实例 / Kafka Producer（均在应用状态上单例）。"""
from fastapi import Request
from sqlalchemy.orm import Session

from app.db.base import get_db  # noqa: F401  转出口供路由统一 import
from app.mq.kafka_producer import MQProducer
from app.services.storage_service import BaseStorage


def get_storage(request: Request) -> BaseStorage:
    return request.app.state.storage


def get_mq(request: Request) -> MQProducer:
    return request.app.state.mq


__all__ = ["get_db", "get_storage", "get_mq", "Session"]
