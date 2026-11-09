from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "eval") not in sys.path:
    sys.path.insert(0, str(ROOT / "eval"))


@pytest.fixture(scope="session")
def db_path() -> Path:
    from sql_agent.config import settings

    path = Path(settings.db_path)
    if not path.exists():
        pytest.skip(f"Chinook 数据库不存在: {path}")
    return path


@pytest.fixture(scope="session")
def catalog(db_path):
    from sql_agent.catalog import DBCatalog

    return DBCatalog(db_path, cache_dir=db_path.parent / ".cache").load()


@pytest.fixture(scope="session")
def semantic():
    from sql_agent.config import settings
    from sql_agent.semantic import SemanticCatalog

    return SemanticCatalog.load(settings.semantic_dir)
