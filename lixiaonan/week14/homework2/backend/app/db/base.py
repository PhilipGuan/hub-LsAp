"""SQLAlchemy 引擎与 Session 管理（§4）。

SQLite 相对路径统一解析到项目根 data/ 目录，避免受启动 CWD 影响。
"""
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import PROJECT_ROOT, get_settings

settings = get_settings()

_url = settings.database_url
_connect_args = {}
if _url.startswith("sqlite"):
    if _url.startswith("sqlite:///./"):
        _url = "sqlite:///" + str((PROJECT_ROOT / _url[len("sqlite:///./"):]).resolve())
    # busy timeout：多进程（API + Worker）并发写 SQLite 时等待锁而非立刻报 database is locked
    _connect_args = {"check_same_thread": False, "timeout": 30}

engine = create_engine(_url, connect_args=_connect_args, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI 依赖：请求级数据库会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """建表（幂等）。SQLite 需确保父目录存在。"""
    if _url.startswith("sqlite"):
        Path(_url.replace("sqlite:///", "")).parent.mkdir(parents=True, exist_ok=True)
    from app.db import models  # noqa: F401  导入以注册表结构

    Base.metadata.create_all(engine)
