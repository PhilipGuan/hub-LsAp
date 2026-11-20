"""测试全局夹具。

要点：
- 在导入任何 app/worker 模块**之前**设置环境变量，让 Settings（lru_cache 单例）与
  db/base 的引擎指向临时目录，不污染开发数据（data/homework2.db）
- LLM 用 mock 后端（无网络依赖）；EMBEDDING 关闭走关键词降级检索（向量链路已在联调覆盖）
- FakeMQProducer 替代真实 Kafka，可注入「发送失败」场景（F1-5）
"""
import os
import tempfile
from pathlib import Path

# ---- 必须先于任何 app/worker 导入 ----
_TMP = Path(tempfile.mkdtemp(prefix="hw2-test-"))
os.environ.update(
    DATABASE_URL=f"sqlite:///{_TMP / 'test.db'}",
    DATA_DIR=str(_TMP),
    LOG_LEVEL="WARNING",
    EMBEDDING_ENABLED="false",
    EMBEDDING_IMAGE_CAPTION="false",
    LLM_BACKEND="mock",
    PARSER_BACKEND="pymupdf",
    ALLOW_DUPLICATE="true",
    MAX_UPLOAD_SIZE_MB="1",
    MAX_UPLOAD_COUNT="3",
    KAFKA_BOOTSTRAP_SERVERS="localhost:9092",
)

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.exceptions import install_exception_handlers
from app.db.base import Base, SessionLocal, engine, init_db
from app.api import api_router
from app.api.deps import get_db, get_mq, get_storage
from app.services.storage_service import LocalStorage


class FakeMQProducer:
    """Kafka Producer 测试桩：记录消息，可注入发送失败。"""

    def __init__(self):
        from app.mq.kafka_producer import KafkaSendError

        self._send_error_cls = KafkaSendError
        self.sent: list = []
        self.fail_next: bool = False

    def send_parse_task(self, msg, key=None) -> None:
        if self.fail_next:
            self.fail_next = False
            raise self._send_error_cls("unit-test injected kafka failure")
        self.sent.append(msg)

    def ping(self) -> bool:
        return True

    def close(self) -> None:
        pass


@pytest.fixture(scope="session")
def settings():
    return get_settings()


@pytest.fixture()
def _clean_db():
    """每个测试重建表结构，保证用例隔离。"""
    init_db()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture()
def storage(settings):
    return LocalStorage(settings)


@pytest.fixture()
def fake_mq():
    return FakeMQProducer()


@pytest.fixture()
def client(_clean_db, storage, fake_mq):
    """带依赖覆盖的 FastAPI 测试客户端（异常处理器与生产一致）。"""
    app = FastAPI()
    install_exception_handlers(app)
    app.include_router(api_router, prefix="/api/v1")

    def override_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_mq] = lambda: fake_mq
    with TestClient(app) as c:
        yield c


# ============ 公共构造工具 ============
def make_pdf_bytes(pages: int = 1, with_image: bool = False) -> bytes:
    """内存生成测试 PDF（pymupdf），可选含位图。"""
    import io

    import pymupdf

    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 72), f"Test page {i + 1}: kafka decouples upload and parse.", fontsize=14)
        if with_image:
            pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 64, 64))
            pix.clear_with(200)
            page.insert_image(pymupdf.Rect(72, 100, 136, 164), pixmap=pix)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def parse_sse(text: str) -> list[dict]:
    """把 SSE 响应文本解析为事件列表。"""
    events = []
    for block in text.split("\n\n"):
        block = block.strip()
        if block.startswith("data:"):
            import json

            events.append(json.loads(block[5:]))
    return events
