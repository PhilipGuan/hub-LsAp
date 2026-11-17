"""解析工作器：从数据库状态机取待解析文档 → MinerU 解析 → 切片/图片入库 → 更新状态。

在链路中的位置：解析链路的执行端。后端事务发件箱把解析请求投递到 Kafka，第一版工作器
先用“数据库状态机轮询”代替消费者（README 允许的最小实现）；解析结果最终写回
切片表/图片资源表，供智能体问答检索。状态流转走 backend.app.services.document，
失败按“重试 3 次 → 死信”推进，不会停在一个中间态。

本地没有 Kafka 时按数据库状态机轮询取任务；Kafka 阶段把 pending_document_ids 换成消费者即可，
消费端仍然必须幂等（indexer 已按“先清理再写入”实现）。

用法（仓库根目录）：
    python parse-worker/worker.py            # 自测（临时库，不依赖外部服务）
    python parse-worker/worker.py --serve    # 常驻处理待解析文档（本地 mock 解析 + 假向量）
    MINERU_API_URL=http://mineru:8000 python parse-worker/worker.py --serve   # 调真实 MinerU
"""

if __package__ in (None, ""):  # 允许直接运行本文件
    import sys
    from pathlib import Path

    for _root in Path(__file__).resolve().parents:
        if (_root / "backend" / "app").is_dir():
            sys.path.insert(0, str(_root))
            break

import argparse
import os
import time
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from backend.agent.config import build_agent_settings
from backend.agent.embedding import EmbeddingClient, embedding_from_settings
from backend.app.clients.storage import ObjectStorage, storage_from_settings
from backend.app.config import Settings, get_settings
from backend.app.db import make_engine, make_session_factory
from backend.app.errors import NotFoundError
from backend.app.models.tables import Document
from backend.app.services import document as document_service

from chunker import prepare
from indexer import index_document
from mineru_runner import MineruParser, parser_from_config

# 状态机里“还需要解析”的状态：queued=首次排队，retrying=失败后等待重试
PENDING_STATUSES = ("queued", "retrying")


@dataclass
class WorkerSettings:
    """工作器配置：轮询间隔、批量、MinerU 接入方式、是否用假向量。"""

    poll_interval_s: float = 1.0
    batch_size: int = 4
    mineru_api_url: str = ""
    mineru_mock: bool | None = None
    mineru_timeout_s: float = 300.0
    stub_models: bool = False

    @classmethod
    def from_env(cls) -> "WorkerSettings":
        """从环境变量读配置（WORKER_* / MINERU_*），不传就用本地默认值。"""
        mock_env = os.getenv("MINERU_MOCK", "").strip().lower()
        mineru_mock: bool | None = None
        if mock_env in {"1", "true", "yes"}:
            mineru_mock = True
        elif mock_env in {"0", "false", "no"}:
            mineru_mock = False
        return cls(
            poll_interval_s=float(os.getenv("WORKER_POLL_INTERVAL", "1.0")),
            batch_size=int(os.getenv("WORKER_BATCH_SIZE", "4")),
            mineru_api_url=os.getenv("MINERU_API_URL", "").strip(),
            mineru_mock=mineru_mock,
            mineru_timeout_s=float(os.getenv("MINERU_TIMEOUT", "300")),
            stub_models=os.getenv("WORKER_STUB_MODELS", "").strip().lower() in {"1", "true", "yes"},
        )


def parser_for(settings: WorkerSettings) -> MineruParser:
    """按配置选解析器：有真实 MinerU 地址用 HTTP，否则用离线 mock。"""
    return parser_from_config(settings.mineru_api_url, settings.mineru_mock, settings.mineru_timeout_s)


def embedder_for(settings: WorkerSettings) -> EmbeddingClient | None:
    """复用智能体的向量客户端配置；本地假模型模式用确定性假向量（与智能体检索一致，维度 8）。"""
    return embedding_from_settings(build_agent_settings(stub=settings.stub_models))


def pending_document_ids(session: Session, limit: int = 4) -> list[str]:
    """按创建时间取一批待解析文档编号（Kafka 阶段的消费者替换点）。"""
    statement = (
        select(Document.id)
        .where(Document.status.in_(PENDING_STATUSES))
        .order_by(Document.created_at)
        .limit(limit)
    )
    return list(session.scalars(statement))


def process_document(
    session: Session,
    doc_id: str,
    storage: ObjectStorage,
    parser: MineruParser,
    embedder: EmbeddingClient | None,
) -> str:
    """处理一个文档；任何异常都转成状态机失败（重试/死信），不向上抛。"""
    info = document_service.get_document(session, doc_id)
    try:
        # 状态机：解析中 → 索引中 → 解析完成；任一步抛异常都交给 record_failure 决定重试还是死信
        document_service.mark_parsing(session, doc_id)
        pdf = storage.get(info.object_key)
        result = parser.parse(pdf, info.filename)
        if not result.markdown.strip():
            raise RuntimeError("MinerU 返回空 Markdown")
        document_service.mark_indexing(session, doc_id)
        chunks, assets = prepare(result)
        stats = index_document(session, doc_id, chunks, assets, storage, embedder)
        document_service.mark_done(session, doc_id, pages=result.pages)
        print(
            f"[worker] 文档 {doc_id} 解析完成: 页数={result.pages} "
            f"切片={stats['chunks']} 图片={stats['assets']}",
            flush=True,
        )
        return "done"
    except Exception as error:  # 单文档失败不影响其它文档
        # 先把事务回滚到干净状态，再让 document_service 写失败状态（重试/死信由它统一判断）
        session.rollback()
        status = document_service.record_failure(session, doc_id, f"{type(error).__name__}: {error}")
        print(f"[worker] 文档 {doc_id} 处理失败 -> {status}: {error}", flush=True)
        return status


def run_once(
    session_factory: sessionmaker[Session],
    storage: ObjectStorage,
    parser: MineruParser,
    embedder: EmbeddingClient | None,
    settings: WorkerSettings,
) -> int:
    """处理一批待解析文档，返回本轮处理数量（每篇文档独立会话，失败互不影响）。"""
    with session_factory() as session:
        doc_ids = pending_document_ids(session, settings.batch_size)
    handled = 0
    for doc_id in doc_ids:
        with session_factory() as session:
            try:
                process_document(session, doc_id, storage, parser, embedder)
            except NotFoundError:
                # 排队后被用户删除：跳过，不算解析失败（否则 worker 会被一条已删文档打断）
                print(f"[worker] 文档 {doc_id} 已不存在（可能被删除），跳过", flush=True)
                continue
            handled += 1
    return handled


def main_loop(settings: WorkerSettings | None = None) -> None:
    """常驻循环：轮询待解析文档；数据库/表还没就绪时等待重试而不是退出。"""
    settings = settings or WorkerSettings.from_env()
    backend_settings = get_settings()
    storage = storage_from_settings(backend_settings)
    engine = make_engine(backend_settings)
    session_factory = make_session_factory(engine)
    parser = parser_for(settings)
    embedder = embedder_for(settings)
    print(
        "[worker] 启动: "
        f"解析器={type(parser).__name__} 向量={'假向量' if isinstance(embedder, object) and type(embedder).__name__ == 'StubEmbedding' else ('真实接口' if embedder else '不生成')} "
        f"轮询={settings.poll_interval_s}s 批量={settings.batch_size}",
        flush=True,
    )
    try:
        while True:
            try:
                handled = run_once(session_factory, storage, parser, embedder, settings)
            except SQLAlchemyError as error:
                # 后端还没建好表 / 数据库暂不可用：等待重试，别让工作器直接挂掉
                print(f"[worker] 数据库暂不可用，{settings.poll_interval_s}s 后重试: {error}", flush=True)
                time.sleep(settings.poll_interval_s)
                continue
            # 有活就马上继续下一轮，没活才按轮询间隔等
            time.sleep(0.1 if handled else settings.poll_interval_s)
    except KeyboardInterrupt:
        print("[worker] 收到中断，退出", flush=True)


def selftest() -> None:
    """离线自测：临时库 + 临时存储 + 假解析 + 假向量，覆盖成功、幂等、自动重试耗尽。"""
    import tempfile
    from pathlib import Path

    from backend.app.common import new_id
    from backend.app.schemas.batch import UploadFileData
    from backend.app.services import upload as upload_service

    class FailingParser:
        """模拟 MinerU 解析超时，用来验证“失败 → 重试 → 死信”状态机。"""

        def parse(self, pdf: bytes, filename: str):
            """直接抛异常。"""
            raise RuntimeError("MinerU 解析超时（自测模拟）")

    tmp = Path(tempfile.mkdtemp(prefix="rag-worker-"))
    settings = Settings(database_url=f"sqlite:///{(tmp / 'worker.db').as_posix()}", data_dir=tmp)
    settings.ensure_dirs()
    engine = make_engine(settings)
    from backend.app.db import init_db

    init_db(engine)
    session_factory = make_session_factory(engine)
    storage = storage_from_settings(settings)
    worker_settings = WorkerSettings(stub_models=True)
    parser = parser_from_config("", mineru_mock=True)
    embedder = embedder_for(worker_settings)

    pdf = b"%PDF-1.7\n" + b"0" * 2048
    with session_factory() as session:
        batch = upload_service.create_batch(
            session,
            settings,
            [UploadFileData(filename="工作器自测.pdf", content=pdf, content_type="application/pdf")],
            storage,
        )
    doc_id = batch.documents[0].doc_id

    handled = run_once(session_factory, storage, parser, embedder, worker_settings)
    print("[parse-worker.worker] 第一轮处理:", handled, "个文档")
    assert handled == 1
    with session_factory() as session:
        info = document_service.get_document(session, doc_id)
        print("[parse-worker.worker] 文档状态:", info.status, "| 页数:", info.pages)
        assert info.status == "done" and info.pages == 3
        from sqlalchemy import func

        from backend.app.models.tables import Asset, Chunk

        chunk_count = session.scalar(select(func.count()).select_from(Chunk).where(Chunk.doc_id == doc_id))
        asset_count = session.scalar(select(func.count()).select_from(Asset).where(Asset.doc_id == doc_id))
        print("[parse-worker.worker] 入库切片:", chunk_count, "| 图片:", asset_count)
        assert chunk_count and chunk_count > 0 and asset_count == 1
        chunks = list(session.scalars(select(Chunk).where(Chunk.doc_id == doc_id)))
        assert all(len(chunk.embedding) == 8 for chunk in chunks), "假向量维度应为 8"

    # 已完成的文档不再被 pending_document_ids 选中，第二轮应为 0（避免重复解析）
    again = run_once(session_factory, storage, parser, embedder, worker_settings)
    print("[parse-worker.worker] 第二轮处理（应无待解析）:", again)
    assert again == 0

    with session_factory() as session:
        batch2 = upload_service.create_batch(
            session,
            settings,
            [UploadFileData(filename="失败链路.pdf", content=b"%PDF-1.7\n" + b"1" * 2048, content_type="application/pdf")],
            storage,
        )
    failing_doc_id = batch2.documents[0].doc_id
    # 连续失败 3 次：每次 record_failure 把状态推进到 retrying，第 3 次到达上限后进死信
    for attempt in range(1, 4):
        handled = run_once(session_factory, storage, FailingParser(), embedder, worker_settings)
        with session_factory() as session:
            info = document_service.get_document(session, failing_doc_id)
        print(f"[parse-worker.worker] 第 {attempt} 次失败后状态:", info.status, "| attempts:", info.attempts)
        assert handled == 1
    with session_factory() as session:
        info = document_service.get_document(session, failing_doc_id)
        assert info.status == "failed" and info.attempts == 3
        from backend.app.models.tables import OutboxEvent

        # 死信事件写进事务发件箱，后端轮询器会把它投递到 DLQ 主题
        dead_letters = list(
            session.scalars(
                select(OutboxEvent).where(
                    OutboxEvent.event_type == "parse.dead_letter",
                    OutboxEvent.aggregate_id == failing_doc_id,
                )
            )
        )
        print("[parse-worker.worker] 死信事件数:", len(dead_letters))
        assert len(dead_letters) == 1
    print("[parse-worker.worker] 自检通过")


def main() -> None:
    """入口：默认跑离线自测，`--serve` 才进常驻轮询。"""
    cli = argparse.ArgumentParser(description="解析工作器")
    cli.add_argument("--serve", action="store_true", help="常驻轮询处理待解析文档（默认只跑离线自测）")
    args = cli.parse_args()
    if args.serve:
        main_loop()
    else:
        selftest()


if __name__ == "__main__":
    main()
