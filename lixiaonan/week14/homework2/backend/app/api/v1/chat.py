"""多模态问答接口（功能3）。

- POST /api/v1/chat  问答（F3-1~F3-8），SSE 流式返回：
  1. 校验问题与附图 -> 2. 检索（向量 / 关键词降级）-> 3. 组装多模态 Prompt（资料图片 +
     用户图片 + 上下文） -> 4. LLM 流式生成 Markdown 回答 -> 5. SSE 事件推送 + 持久化
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_storage
from app.schemas.chat import ChatRequest
from app.services import chat_service
from app.services.storage_service import BaseStorage

router = APIRouter()


@router.post("")
async def chat(
    req: ChatRequest,
    db: Session = Depends(get_db),
    storage: BaseStorage = Depends(get_storage),
):
    """多模态问答（SSE）。事件类型：meta / status / chunk / done / error。"""
    return await chat_service.chat_sse_response(req, db, storage)
