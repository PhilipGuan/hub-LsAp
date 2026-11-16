import os
import uuid
from typing import List, Optional

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import config
import redis_utils
import kafka_utils
import llm_utils
import es_utils

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

app = FastAPI(title="多模态 RAG 问答")


@app.get("/", response_class=HTMLResponse)
def index():
    with open(os.path.join(STATIC_DIR, "index.html"), "r", encoding="utf-8") as f:
        return f.read()


@app.post("/api/upload")
async def upload(files: List[UploadFile] = File(...)):
    results = []
    for f in files:
        doc_id = uuid.uuid4().hex
        filename = f.filename or "unknown.pdf"
        filepath = os.path.join(config.UPLOAD_DIR, f"{doc_id}_{filename}")
        with open(filepath, "wb") as fh:
            fh.write(await f.read())

        redis_utils.add_document(doc_id, filename)      # 状态置为 pending
        kafka_utils.publish_task(doc_id, filename, filepath)

        results.append({"doc_id": doc_id, "filename": filename,
                        "status": redis_utils.PENDING})
    return {"results": results}


@app.get("/api/documents")
def documents():
    return {"documents": redis_utils.list_documents()}


class AskRequest(BaseModel):
    question: str
    images: Optional[List[str]] = None   # 图片 data URL 列表（可空）


@app.post("/api/ask")
def ask(req: AskRequest):
    # RAG：向量检索相关切块
    hits = []
    try:
        qvec = llm_utils.embed_texts([req.question])[0]
        hits = es_utils.search(qvec, top_k=4)
    except Exception:
        hits = []

    context = "\n\n".join(h["text"] for h in hits)

    # 收集检索切块关联的图片 → 图文混排返回
    related = []
    for h in hits:
        for img in h.get("images", []):
            related.append("/files/" + img.replace(os.sep, "/"))
    related = list(dict.fromkeys(related))[:6]

    prompt = (
        "请根据下面的资料回答问题，如果资料不足以回答请明确说明。\n\n"
        f"资料：\n{context}\n\n问题：{req.question}"
    )
    answer = llm_utils.ask_multimodal(prompt, req.images or [])
    return {"answer": answer, "images": related}


@app.get("/files/{path:path}")
def serve_file(path: str):
    base = os.path.normpath(config.PARSE_DIR)
    safe = os.path.normpath(os.path.join(base, path))
    if not safe.startswith(base + os.sep):
        raise HTTPException(status_code=403)
    if not os.path.exists(safe):
        raise HTTPException(status_code=404)
    return FileResponse(safe)


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")