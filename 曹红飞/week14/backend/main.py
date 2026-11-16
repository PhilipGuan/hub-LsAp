"""FastAPI 入口：上传、状态查询、图片访问、多模态问答与静态前端。"""
import io
import logging
import shutil
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image

from . import config, docs_db, embedder, kafka_client, milvus_store, qa, worker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.make_data_dirs()
    docs_db.init_db()
    milvus_store.create_collection_if_missing()
    embedder.start_warmup()
    kafka_client.start_consumer(worker.handle_parse_message)
    yield


app = FastAPI(title="多模态RAG问答", lifespan=lifespan)


@app.post("/api/upload")
async def upload(files: list[UploadFile] = File(...)):
    """接收多个 PDF：保存文件、登记状态、发送 Kafka 解析消息。"""
    saved = []
    for f in files:
        if not f.filename or not f.filename.lower().endswith(".pdf"):
            continue
        doc_id = uuid.uuid4().hex
        dest = config.UPLOADS_DIR / f"{doc_id}.pdf"
        with open(dest, "wb") as out:
            shutil.copyfileobj(f.file, out)
        docs_db.add_doc(doc_id, f.filename)
        sent = kafka_client.send_parse_request(doc_id, dest, f.filename)
        saved.append({
            "doc_id": doc_id,
            "filename": f.filename,
            "status": docs_db.STATUS_PENDING,
            "kafka_sent": sent,
        })
    if not saved:
        raise HTTPException(400, "没有收到 PDF 文件")
    return {"uploaded": saved}


@app.get("/api/docs")
def list_docs():
    """文档列表及解析状态。"""
    return {"docs": docs_db.list_docs()}


@app.get("/api/docs/{doc_id}/images/{filename:path}")
def get_image(doc_id: str, filename: str):
    """访问某文档解析出的图片。"""
    img = (config.PARSED_DIR / doc_id / "images" / filename).resolve()
    root = config.PARSED_DIR.resolve()
    if not str(img).startswith(str(root)) or not img.is_file():
        raise HTTPException(404, "图片不存在")
    return FileResponse(img)


@app.post("/api/ask")
def ask(question: str = Form(""), image: UploadFile | None = File(None)):
    """多模态问答：文本问题 + 可选图片。"""
    query_image = None
    if image is not None and image.filename:
        data = image.file.read()
        try:
            query_image = Image.open(io.BytesIO(data)).convert("RGB")
        except Exception:
            raise HTTPException(400, "无法解析上传的图片")
    if not question.strip() and query_image is None:
        raise HTTPException(400, "问题与图片不能同时为空")
    try:
        return qa.ask(question.strip(), query_image)
    except Exception as e:
        log.exception("问答失败")
        raise HTTPException(500, f"问答失败: {e}")


# 静态前端（挂在最后，不影响 /api 路由）
app.mount("/", StaticFiles(directory=config.FRONTEND_DIR, html=True), name="frontend")
