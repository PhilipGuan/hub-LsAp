from uuid import uuid4

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

import app as app_module
from sql_agent import graph as graph_module
from sql_agent.models import SQLDraft, TableSelection


client = TestClient(app_module.app)


class FakeStructured:
    def __init__(self, owner, schema):
        self.owner = owner
        self.schema = schema

    def invoke(self, messages):
        if self.schema is TableSelection:
            return TableSelection(tables=["customers"], reason="统计客户")
        if self.schema is SQLDraft:
            return SQLDraft(sql="SELECT COUNT(*) AS total FROM customers")
        raise AssertionError("unexpected schema")


class FakeLLM:
    def with_structured_output(self, schema):
        return FakeStructured(self, schema)

    def invoke(self, messages):
        return AIMessage(content="数据库中共有 59 位客户。")


def test_health_endpoint():
    response = client.get("/api/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["database_tables"] > 0


def test_index_page_is_served():
    response = client.get("/")
    assert response.status_code == 200
    assert "LangGraph SQL Agent" in response.text


def test_chat_rejects_invalid_thread_id():
    response = client.post(
        "/api/chat",
        json={"message": "查询客户数量", "thread_id": "bad id!"},
    )
    assert response.status_code == 400


def test_stream_endpoint_returns_final_result(monkeypatch):
    monkeypatch.setattr(graph_module, "get_llm", lambda: FakeLLM())

    response = client.post(
        "/api/chat/stream",
        json={"message": "数据库里有多少客户？", "thread_id": str(uuid4())},
    )

    assert response.status_code == 200
    assert "event: step" in response.text
    assert "event: result" in response.text
    assert "数据库中共有 59 位客户。" in response.text
    assert "SELECT COUNT(*) AS total FROM customers" in response.text

