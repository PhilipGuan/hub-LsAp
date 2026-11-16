"""API 路由聚合（前缀 /api/v1，§5）。"""
from fastapi import APIRouter

from app.api.v1 import chat, conversations, documents

api_router = APIRouter()
api_router.include_router(documents.router, prefix="/documents", tags=["documents"])
api_router.include_router(chat.router, prefix="/chat", tags=["chat"])
api_router.include_router(conversations.router, prefix="/conversations", tags=["conversations"])
