"""切片与图片资源入库：先清理再写入（幂等，重投消息不会产生重复切片）+ 向量生成。

在链路中的位置：解析工作器在 MinerU 解析 + chunker 切片之后调用 `index_document()`，
把文本切片与图片资源写进数据库、图片写进对象存储、切片写向量；状态流转由工作器负责。

为什么要“先清理再写入”：Kafka/轮询都可能重复投递同一文档的解析结果，直接追加会产生
重复切片，问答检索时就会命中文不对题的重复内容；先删后写让每次解析结果都是幂等的。
"""

if __package__ in (None, ""):  # 允许直接运行本文件
    import sys
    from pathlib import Path

    for _root in Path(__file__).resolve().parents:
        if (_root / "backend" / "app").is_dir():
            sys.path.insert(0, str(_root))
            break

import re

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from backend.app.clients.storage import ObjectStorage
from backend.app.common import new_id
from backend.app.models.tables import Asset, Chunk

from chunker import AssetDraft, ChunkDraft

# 对象存储的 key 只允许安全字符：图片名来自 MinerU 产物，不能直接拼进路径
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]")


def clear_document(session: Session, doc_id: str) -> tuple[int, int]:
    """删除该文档已有的切片与图片资源（消费幂等：重跑不会重复）。"""
    chunks = session.execute(delete(Chunk).where(Chunk.doc_id == doc_id)).rowcount or 0
    assets = session.execute(delete(Asset).where(Asset.doc_id == doc_id)).rowcount or 0
    return int(chunks), int(assets)


def write_assets(session: Session, doc_id: str, assets: list[AssetDraft], storage: ObjectStorage) -> list[str]:
    """图片写入对象存储并落库，返回 asset_id 列表（顺序与入参一致）。"""
    asset_ids: list[str] = []
    for index, draft in enumerate(assets):
        safe_name = _SAFE_NAME.sub("_", draft.name or "") or f"image-{index + 1}.png"
        object_key = f"assets/{doc_id}/{safe_name}"
        # 先落对象存储再落库：库里出现行时，图片一定已经可读
        storage.put(object_key, draft.data, "image/png")
        asset_id = new_id("asset")
        session.add(
            Asset(
                id=asset_id,
                doc_id=doc_id,
                page=draft.page,
                asset_type=draft.asset_type,
                object_key=object_key,
                caption=draft.caption,
                ocr_text=draft.ocr_text,
                bbox=draft.bbox,
            )
        )
        asset_ids.append(asset_id)
    return asset_ids


def _embed_texts(embedder, texts: list[str]) -> list[list[float] | None]:
    """批量向量化；批量失败时逐条重试，单条仍失败就写 None（检索自动降级为关键词）。

    外部向量接口偶尔对个别切片返回 400（超长/特殊字符），不能让整篇文档解析失败：
    失败的那条切片没有向量，text_search 会按关键词兜底，其余切片不受影响。
    """
    if embedder is None or not texts:
        return [None] * len(texts)
    try:
        vectors = list(embedder.embed(texts))
        if len(vectors) == len(texts):
            return vectors
    except Exception as error:
        print(f"[indexer] 批量向量化失败，改为逐条重试: {error}", flush=True)
    vectors = []
    for text in texts:
        try:
            vectors.append(list(embedder.embed([text]))[0])
        except Exception:
            vectors.append(None)
    failed = sum(1 for vector in vectors if vector is None)
    if failed:
        print(f"[indexer] {failed} 条切片向量化失败，已降级为关键词检索", flush=True)
    return vectors


def write_chunks(
    session: Session,
    doc_id: str,
    chunks: list[ChunkDraft],
    asset_ids: list[str],
    embedder=None,
) -> int:
    """切片落库并生成向量；切片与同页图片的关联记在 asset_id 上。"""
    texts = [draft.text for draft in chunks]
    # 没有向量客户端或单条向量化失败时 embedding 写 None：智能体检索会自动降级为关键词匹配
    vectors = _embed_texts(embedder, texts)
    for draft, vector in zip(chunks, vectors):
        # 一个切片只关联一张主图（表结构只有一个 asset_id），多图按页内顺序取第一张
        asset_id = None
        if draft.asset_indexes and draft.asset_indexes[0] < len(asset_ids):
            asset_id = asset_ids[draft.asset_indexes[0]]
        session.add(
            Chunk(
                id=new_id("chunk"),
                doc_id=doc_id,
                page=draft.page,
                seq=draft.seq,
                text=draft.text,
                asset_id=asset_id,
                embedding=vector,
            )
        )
    return len(chunks)


def index_document(
    session: Session,
    doc_id: str,
    chunks: list[ChunkDraft],
    assets: list[AssetDraft],
    storage: ObjectStorage,
    embedder=None,
) -> dict[str, int]:
    """一次解析产物的完整入库（幂等）：返回写入的切片数与图片数。"""
    if not chunks:
        raise RuntimeError("解析结果没有可用切片")
    removed_chunks, removed_assets = clear_document(session, doc_id)
    asset_ids = write_assets(session, doc_id, assets, storage)
    chunk_count = write_chunks(session, doc_id, chunks, asset_ids, embedder)
    # 三件事（图片落库 / 切片落库 / 向量）放在同一个事务里提交，避免半成品数据
    session.commit()
    return {
        "chunks": chunk_count,
        "assets": len(asset_ids),
        "removed_chunks": removed_chunks,
        "removed_assets": removed_assets,
    }


def main() -> None:
    """自检：首次入库、重跑去重（幂等）、向量写入、图文关联、对象存储落盘。"""
    import tempfile
    from pathlib import Path

    from sqlalchemy import create_engine

    from backend.agent.embedding import StubEmbedding
    from backend.app.common import new_id, utcnow
    from backend.app.models.tables import Base, Batch, Document

    from chunker import prepare
    from mineru_runner import MockMineruParser

    tmp = Path(tempfile.mkdtemp(prefix="rag-indexer-"))
    engine = create_engine(f"sqlite:///{(tmp / 'index.db').as_posix()}")
    Base.metadata.create_all(engine)
    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    storage_root = tmp / "storage"

    from backend.app.clients.storage import LocalStorage

    storage = LocalStorage(storage_root)
    embedder = StubEmbedding(dim=8)

    chunks, assets = prepare(MockMineruParser().parse(b"%PDF-1.7\n" + b"0" * 2048, "入库测试.pdf"))
    batch_id, doc_id = new_id("batch"), new_id("doc")
    with factory() as session:
        session.add(Batch(id=batch_id, trace_id=new_id("trace"), file_count=1))
        session.add(
            Document(
                id=doc_id,
                batch_id=batch_id,
                filename="入库测试.pdf",
                sha256="0" * 64,
                object_key=f"pdfs/{doc_id}.pdf",
                status="indexing",
                pages=3,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        )
        session.commit()

        first = index_document(session, doc_id, chunks, assets, storage, embedder)
        second = index_document(session, doc_id, chunks, assets, storage, embedder)
        print("[parse-worker.indexer] 第一次入库:", first)
        print("[parse-worker.indexer] 第二次入库（应幂等）:", second)
        assert first["chunks"] == len(chunks) and first["assets"] == len(assets)
        assert second["removed_chunks"] == first["chunks"], "重跑应先清掉旧切片"
        assert second["chunks"] == first["chunks"] and second["assets"] == first["assets"], "重跑不能重复入库"

        chunk_rows = list(session.scalars(select(Chunk).where(Chunk.doc_id == doc_id).order_by(Chunk.seq)))
        asset_rows = list(session.scalars(select(Asset).where(Asset.doc_id == doc_id)))
        assert len(chunk_rows) == len(chunks) and len(asset_rows) == len(assets)
        assert all(len(row.embedding) == 8 for row in chunk_rows), "切片应写入向量"
        linked = [row for row in chunk_rows if row.asset_id]
        assert linked, "至少一个切片应关联图片"
        assert linked[0].asset_id in {row.id for row in asset_rows}
        for row in asset_rows:
            assert storage.exists(row.object_key), f"图片应写入对象存储: {row.object_key}"
        print("[parse-worker.indexer] 切片:", len(chunk_rows), "| 图片:", len(asset_rows), "| 向量维度:", len(chunk_rows[0].embedding))
        print("[parse-worker.indexer] 自检通过")


if __name__ == "__main__":
    main()
