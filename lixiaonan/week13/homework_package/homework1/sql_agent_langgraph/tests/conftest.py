"""测试共享夹具：fake 结构化 LLM、fake catalog、真实 chinook.db 组件。"""

from __future__ import annotations

from pathlib import Path

import pytest

from sql_agent_langgraph.config import settings
from sql_agent_langgraph.db.catalog import SchemaCatalog
from sql_agent_langgraph.db.executor import SQLExecutor
from sql_agent_langgraph.db.validator import SQLValidator


class FakeStructuredLLM:
    """with_structured_output 链的替身：按队列依次返回预置结果。

    - 队列元素是 Pydantic 实例则直接返回；
    - 是 Exception 实例则抛出（用于测降级路径）；
    - 记录全部 prompt 供断言调用次数与内容。
    """

    def __init__(self, results: list) -> None:
        self._results = list(results)
        self.calls: list[str] = []

    def invoke(self, prompt, *args, **kwargs):
        self.calls.append(str(prompt))
        if not self._results:
            raise AssertionError("FakeStructuredLLM 结果已耗尽：出现了多余的 LLM 调用")
        item = self._results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    @property
    def call_count(self) -> int:
        return len(self.calls)


class FakeCatalog:
    """SchemaCatalog 的轻量替身：只实现路由与节点用到的接口。"""

    def __init__(self, table_names: list[str], connected: set[frozenset[str]] | None = None):
        self.table_names = list(table_names)
        # 两两连通的表组合；未列出的多表组合视为不连通。
        self._connected = connected or set()

    def is_connected(self, tables: list[str]) -> bool:
        if len(tables) <= 1:
            return True
        return frozenset(tables) in self._connected

    def render_catalog(self) -> str:
        return "可用表：" + ", ".join(self.table_names)

    def render_schema(self, tables: list[str]) -> str:
        return f"schema of {', '.join(tables)}"


@pytest.fixture(scope="session")
def db_path() -> Path:
    path = settings.db_path
    if not path.exists():
        pytest.skip(f"chinook.db 不存在：{path}")
    return path


@pytest.fixture(scope="session")
def real_catalog(db_path) -> SchemaCatalog:
    return SchemaCatalog(db_path)


@pytest.fixture(scope="session")
def validator(db_path) -> SQLValidator:
    return SQLValidator(db_path)


@pytest.fixture(scope="session")
def executor(db_path) -> SQLExecutor:
    return SQLExecutor(db_path)
