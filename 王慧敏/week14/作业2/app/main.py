# -*- coding: utf-8 -*-
"""FastAPI 后端：PDF 上传、解析状态查询、多模态 RAG 问答。

运行方式:
    uvicorn app.main:app --reload --port 8000

接口:
    POST /api/documents/upload     上传多个 PDF，返回每个文档的 doc_id（状态=待解析）
    GET  /api/documents             查询所有文档及其解析状态（前端轮询用）
    GET  /api/documents/{doc_id}   查询单个文档状态
    POST /api/chat                  多模态 RAG 问答
"""

import os
import shutil
import uuid

from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app import db
from app.config import UPLOAD_DIR
from app.kafka_bus import send_parse_task
from app.rag_service import answer_question

app = FastAPI(title="Kafka 多模态 RAG 问答")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    db.init_db()


@app.post("/api/documents/upload")
async def upload_documents(files: list[UploadFile] = File(...)):
    """上传多个 PDF：落盘 -> 写 SQLite(待解析) -> 发 Kafka 解析任务。"""
    results = []
    for f in files:
        doc_id = uuid.uuid4().hex
        safe_name = f.filename or f"{doc_id}.pdf"
        dest_path = os.path.join(UPLOAD_DIR, f"{doc_id}_{safe_name}")
        with open(dest_path, "wb") as out:
            shutil.copyfileobj(f.file, out)

        db.create_document(filename=safe_name, file_path=dest_path, doc_id=doc_id)
        send_parse_task(doc_id, safe_name, dest_path)
        results.append({"doc_id": doc_id, "filename": safe_name, "status": "待解析"})

    return {"documents": results}


@app.get("/api/documents")
def list_documents():
    return {"documents": db.list_documents()}


@app.get("/api/documents/{doc_id}")
def get_document(doc_id: str):
    doc = db.get_document(doc_id)
    if not doc:
        return {"error": "文档不存在"}
    return doc


class ChatRequest(BaseModel):
    question: str
    doc_ids: list[str] | None = None
    # 用户提问时附带的图片，前端已读成 data URL（data:image/xxx;base64,....）
    image_data_urls: list[str] | None = None


@app.post("/api/chat")
def chat(req: ChatRequest):
    result = answer_question(
        question=req.question,
        doc_ids=req.doc_ids,
        user_image_data_urls=req.image_data_urls,
    )
    return result
