import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from backend.api import documents as document_routes, qa as qa_routes
from backend.config import load_config
from backend.logging_setup import error_context
from backend.service.documents import DocumentError, Documents
from backend.service.index import Index
from backend.service.model_clients import Embedding, Vision
from backend.service.qa import QA


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = load_config()
    index = Index(config)
    documents = Documents(config, index)
    embedding, vision = Embedding(config), Vision(config)
    app.state.documents = documents
    app.state.qa = QA(documents, index, embedding, vision)
    try:
        yield
    finally:
        index.client.close()
        documents.redis.close()
        embedding.client.close()
        vision.client.close()


app = FastAPI(title="VLM RAG", lifespan=lifespan)
app.include_router(document_routes.router)
app.include_router(qa_routes.router)


@app.middleware("http")
async def report_errors(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception as exc:
        route = getattr(request.scope.get("route"), "path", "-")
        logger.error("request_failed method=%s route=%s error=%s",
                     request.method, route, error_context(exc))
        return JSONResponse({"detail": "后端服务异常，请查看服务器日志。"}, status_code=500)


@app.exception_handler(DocumentError)
async def document_error(request: Request, exc: DocumentError):
    return JSONResponse({"detail": str(exc)}, status_code=exc.status_code)
