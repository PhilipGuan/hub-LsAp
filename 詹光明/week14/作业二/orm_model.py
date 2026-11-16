"""
sqlite 元信息存储：记录上传的文件以及它的解析状态。

状态流转：
    待解析 -> 解析中 -> 解析完成
                      -> 解析失败（可点击“重新解析”再次投递 Kafka）
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text, create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

from config import DB_PATH

Base = declarative_base()

STATE_PENDING = "待解析"
STATE_PROCESSING = "解析中"
STATE_DONE = "解析完成"
STATE_FAILED = "解析失败"

ALL_STATES = (STATE_PENDING, STATE_PROCESSING, STATE_DONE, STATE_FAILED)


class File(Base):
    """文件表：元信息 + 解析状态 + 解析产物路径"""

    __tablename__ = "files"

    id = Column(Integer, primary_key=True, autoincrement=True)
    filename = Column(String(512), nullable=False)          # 原始文件名
    filepath = Column(String(1024), nullable=False)         # 本地保存路径
    sha256 = Column(String(64), index=True)                 # 文件指纹，用于去重
    filesize = Column(Integer, default=0)

    filestate = Column(String(20), nullable=False, default=STATE_PENDING)
    md_path = Column(String(1024))                          # 解析后的 markdown 路径
    image_dir = Column(String(1024))                        # markdown 里图片的相对目录
    error = Column(Text)                                    # 失败原因

    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = {"sqlite_autoincrement": True}

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<File id={self.id} name={self.filename} state={self.filestate}>"


engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
    future=True,
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_conn, _connection_record):
    """WAL + busy_timeout：允许 web 进程和 worker 进程同时读写。"""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine, expire_on_commit=False)


# ---------------------------------------------------------------------------
# 便捷操作
# ---------------------------------------------------------------------------
def create_file(filename: str, filepath: str, sha256: str = "", filesize: int = 0) -> int:
    with Session() as session:
        record = File(
            filename=filename,
            filepath=filepath,
            sha256=sha256,
            filesize=filesize,
            filestate=STATE_PENDING,
        )
        session.add(record)
        session.commit()
        return record.id


def get_file(file_id: int):
    with Session() as session:
        return session.get(File, int(file_id))


def find_by_sha(sha256: str):
    if not sha256:
        return None
    with Session() as session:
        return (
            session.query(File)
            .filter(File.sha256 == sha256)
            .order_by(File.id.desc())
            .first()
        )


def list_files():
    with Session() as session:
        return session.query(File).order_by(File.id.desc()).all()


def update_file(file_id: int, **fields) -> bool:
    """更新文件记录，例如 update_file(1, filestate=STATE_PROCESSING)。"""
    with Session() as session:
        record = session.get(File, int(file_id))
        if record is None:
            return False
        for key, value in fields.items():
            if hasattr(record, key):
                setattr(record, key, value)
        record.updated_at = datetime.now()
        session.commit()
        return True


def pop_file(file_id: int):
    """删除记录并返回删除前的关键信息（供清理磁盘 / 向量用）。"""
    with Session() as session:
        record = session.get(File, int(file_id))
        if record is None:
            return None
        info = {
            "id": record.id,
            "filename": record.filename,
            "filepath": record.filepath,
            "md_path": record.md_path,
            "image_dir": record.image_dir,
        }
        session.delete(record)
        session.commit()
        return info
