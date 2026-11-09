from uuid import uuid4

from langchain_core.messages import AIMessage

from sql_agent import graph as graph_module
from sql_agent.models import SQLDraft, TableSelection


class FakeStructuredRunnable:
    def __init__(self, owner, schema):
        self.owner = owner
        self.schema = schema

    def invoke(self, messages):
        if self.schema is TableSelection:
            return TableSelection(tables=["customers"], reason="统计客户数量")
        if self.schema is SQLDraft:
            self.owner.sql_calls += 1
            if self.owner.force_first_error and self.owner.sql_calls == 1:
                return SQLDraft(sql="SELECT missing_column FROM customers")
            return SQLDraft(sql="SELECT COUNT(*) AS total FROM customers")
        raise AssertionError(f"unexpected schema: {self.schema}")


class FakeLLM:
    def __init__(self, force_first_error=False):
        self.force_first_error = force_first_error
        self.sql_calls = 0

    def with_structured_output(self, schema):
        return FakeStructuredRunnable(self, schema)

    def invoke(self, messages):
        return AIMessage(content="Chinook 数据库中一共有 59 位客户。")


def test_graph_success_path(monkeypatch):
    fake = FakeLLM()
    monkeypatch.setattr(graph_module, "get_llm", lambda: fake)

    config = graph_module.make_config(str(uuid4()))
    state = graph_module.sql_agent.invoke(
        graph_module.agent_input("数据库里有多少客户？"),
        config=config,
    )

    assert state["answer"] == "Chinook 数据库中一共有 59 位客户。"
    assert state["sql"] == "SELECT COUNT(*) AS total FROM customers"
    assert state["retry_count"] == 0
    assert state["rows"][0]["total"] > 0


def test_graph_repairs_invalid_sql(monkeypatch):
    fake = FakeLLM(force_first_error=True)
    monkeypatch.setattr(graph_module, "get_llm", lambda: fake)

    config = graph_module.make_config(str(uuid4()))
    state = graph_module.sql_agent.invoke(
        graph_module.agent_input("数据库里有多少客户？"),
        config=config,
    )

    assert fake.sql_calls == 2
    assert state["retry_count"] == 1
    assert state["sql_error"] == ""
    assert state["sql"] == "SELECT COUNT(*) AS total FROM customers"
