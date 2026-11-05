"""Web 接口测试：不调用 LLM，只验证请求契约、静态页和密钥边界。"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import sql_agent.api as api_module
from sql_agent.config import settings
from sql_agent.llm import LLMRuntimeConfig, current_llm_config
from sql_agent.models import WorkflowOutput


class _FakeWorkflow:
    def __init__(self) -> None:
        self.catalog = SimpleNamespace(
            db_path=settings.db_path,
            table_names=["albums", "tracks"],
            table_overview=lambda: [
                {"name": "albums", "rows": 347, "columns": 3},
                {"name": "tracks", "rows": 3503, "columns": 9},
            ],
        )
        self.questions: list[str] = []
        self.configs: list[LLMRuntimeConfig] = []

    async def run(self, question: str) -> WorkflowOutput:
        self.questions.append(question)
        self.configs.append(current_llm_config())
        return WorkflowOutput(
            question=question,
            answer="共 347 张专辑。",
            sql="SELECT COUNT(*) AS n FROM albums",
            tables=["albums"],
            columns=["n"],
            rows=[["347"]],
            row_count=1,
            evidence=["347"],
        )


@pytest.fixture
def client_and_workflow(monkeypatch):
    fake = _FakeWorkflow()
    monkeypatch.setattr(api_module, "_get_workflow", lambda: fake)
    with TestClient(api_module.app) as client:
        yield client, fake


def test_config_never_returns_api_key(client_and_workflow, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-server-secret")
    client, _ = client_and_workflow

    response = client.get("/api/config")

    assert response.status_code == 200
    payload = response.json()
    assert payload["has_server_api_key"] is True
    assert payload["database"]["file"] == "chinook.db"
    assert len(payload["database"]["tables"]) == 2
    assert "sk-server-secret" not in response.text


def test_query_is_rejected_without_any_api_key(client_and_workflow, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client, fake = client_and_workflow

    response = client.post("/api/query", json={"question": "总共有多少张专辑？"})

    assert response.status_code == 400
    assert "API Key" in response.json()["detail"]
    assert fake.questions == []


def test_query_applies_runtime_overrides(client_and_workflow) -> None:
    client, fake = client_and_workflow

    response = client.post(
        "/api/query",
        json={
            "question": "总共有多少张专辑？",
            "llm": {
                "base_url": "http://127.0.0.1:9999/v1",
                "api_key": "sk-user-secret",
                "model": "primary-test",
                "summary_model": "summary-test",
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["rows"] == [["347"]]
    assert payload["columns"] == ["n"]
    assert payload["answer"] == "共 347 张专辑。"
    assert "sk-user-secret" not in response.text

    assert fake.questions == ["总共有多少张专辑？"]
    assert fake.configs == [
        LLMRuntimeConfig(
            base_url="http://127.0.0.1:9999/v1",
            api_key="sk-user-secret",
            primary_model="primary-test",
            summary_model="summary-test",
        )
    ]
    # 请求结束后覆盖值必须被清理，不能泄漏到后续请求。
    assert current_llm_config() == LLMRuntimeConfig()


def test_query_falls_back_to_server_key(client_and_workflow, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-server-secret")
    client, fake = client_and_workflow

    response = client.post("/api/query", json={"question": "总共有多少张专辑？"})

    assert response.status_code == 200
    assert fake.configs[0].api_key is None


def test_query_validates_question_and_base_url(client_and_workflow) -> None:
    client, _ = client_and_workflow

    blank = client.post("/api/query", json={"question": "   "})
    assert blank.status_code == 422

    bad_url = client.post(
        "/api/query",
        json={"question": "test", "llm": {"base_url": "file:///tmp/chinook.db"}},
    )
    assert bad_url.status_code == 422


def test_static_page_and_assets_are_mounted(client_and_workflow) -> None:
    client, _ = client_and_workflow

    index = client.get("/")
    assert index.status_code == 200
    assert "layui.js" in index.text
    assert "app.js" in index.text

    assert client.get("/static/assets/app.css").status_code == 200
    assert client.get("/static/assets/app.js").status_code == 200
    assert client.get("/static/vendor/layui/layui.js").status_code == 200
    assert client.get("/api/health").json() == {"status": "ok"}


def test_web_directory_contains_layui_bundle() -> None:
    web_dir = Path(api_module.WEB_DIR)
    assert (web_dir / "index.html").exists()
    assert (web_dir / "vendor" / "layui" / "layui.js").exists()
    assert (web_dir / "vendor" / "layui" / "css" / "layui.css").exists()
