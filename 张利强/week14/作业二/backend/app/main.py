import asyncio
import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api import chat, documents, events, sessions
from .config import STORAGE_ROOT, get_settings
from .db import init_db
from .events import bus
from .services.es_store import ensure_index
from .status_consumer import status_consumer_loop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [api] %(message)s",
)
log = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_dirs()
    init_db()
    try:
        ensure_index()
    except Exception:  # noqa: BLE001
        log.warning("Elasticsearch not ready, index init skipped")

    bus.bind_loop(asyncio.get_running_loop())
    threading.Thread(target=status_consumer_loop, daemon=True, name="status-consumer").start()
    log.info("mock_llm=%s mock_embedding=%s", settings.use_mock_llm, settings.use_mock_embedding)
    yield


app = FastAPI(title="Kafka 多模态 RAG 问答", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(documents.router)
app.include_router(sessions.router)
app.include_router(chat.router)
app.include_router(events.router)

STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=STORAGE_ROOT), name="static")


@app.get("/")
def root():
    return {"service": "multimodal-rag", "docs": "/docs"}
