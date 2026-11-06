"""FastAPI 层测试：复用 workflow 测试中“mock LLM、真实数据库”的手法。

验证 Web 适配层本身的职责（请求校验、路由、响应结构、错误处理），
不重复 workflow 内部状态机的覆盖（那部分由 ``test_workflow.py`` 负责）。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from sql_agent.llm import StructuredAgent
from sql_agent.models import FinalAnswer, SQLDraft, TableSelection


def _patch_agents(monkeypatch, outputs):
    calls = []

    async def fake_ainvoke(self, text):
        calls.append((self.name, text))
        return outputs[len(calls) - 1]

    monkeypatch.setattr(StructuredAgent, "ainvoke", fake_ainvoke)
    return calls


def test_query_happy_path(monkeypatch) -> None:
    from sql_agent.api import app

    outputs = [
        TableSelection(tables=["albums"], reason="统计专辑数量", confidence=0.9),
        SQLDraft(sql="SELECT COUNT(*) AS n FROM albums", explanation="count"),
        FinalAnswer(answer="共 347 张专辑。", evidence=["347"]),
    ]
    _patch_agents(monkeypatch, outputs)

    with TestClient(app) as client:
        resp = client.post("/api/query", json={"question": "总共有多少张专辑？"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["error"] is None
    assert data["answer"] == "共 347 张专辑。"
    assert data["tables"] == ["albums"]
    assert data["row_count"] == 1


def test_query_rejects_empty_question() -> None:
    from sql_agent.api import app

    with TestClient(app) as client:
        resp = client.post("/api/query", json={"question": "   "})

    assert resp.status_code == 400


def test_query_rejects_missing_question() -> None:
    from sql_agent.api import app

    with TestClient(app) as client:
        resp = client.post("/api/query", json={})

    assert resp.status_code == 422


def test_healthz() -> None:
    from sql_agent.api import app

    with TestClient(app) as client:
        resp = client.get("/healthz")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_index_page_served() -> None:
    from sql_agent.api import app

    with TestClient(app) as client:
        resp = client.get("/")

    assert resp.status_code == 200
    assert "Chinook SQL Agent" in resp.text
