"""pytest 全局配置。

必须在导入 ``app.*`` 之前设置环境变量，因此这些赋值放在模块顶层：
* 关闭 Kafka，走进程内直连队列（测试不依赖外部 broker）
* 强制内置解析器（不需要下载 MinerU 模型）
* 打开 LLM_MOCK（不调用真实大模型）
* 数据目录指向临时目录，避免污染 data/
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="rag-test-"))

os.environ.update(
    {
        "DATA_DIR": str(TEST_DATA_DIR),
        "KAFKA_ENABLED": "0",
        "KAFKA_FALLBACK_DIRECT": "1",
        "EMBED_WORKER": "1",
        "PARSER_BACKEND": "lite",
        "LLM_MOCK": "1",
        "RETRIEVAL_MODE": "bm25",
        "RAG_CAPTION_QUERY": "0",
        "OPEN_BROWSER": "0",
        "WORKER_POLL_MS": "50",
    }
)

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def client():
    """带 lifespan 的 TestClient：会真正启动解析 worker 线程。"""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
