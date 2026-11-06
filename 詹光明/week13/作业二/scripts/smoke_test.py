"""离线冒烟测试：不调用大模型，验证服务与前端静态资源。

用法：
    python scripts/smoke_test.py
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = (PROJECT_ROOT.parent / "作业一").resolve()
for path in (str(PROJECT_ROOT), str(BACKEND_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402
from sql_agent.models import (  # noqa: E402
    ExecutionResult,
    TableSelection,
    ValidationResult,
    WorkflowOutput,
)


def check_index(client: TestClient) -> None:
    response = client.get("/")
    print(f"GET / -> {response.status_code}")
    assert response.status_code == 200
    assert "Chinook" in response.text

    for asset in ("/static/styles.css", "/static/app.js"):
        asset_response = client.get(asset)
        print(f"GET {asset} -> {asset_response.status_code}")
        assert asset_response.status_code == 200


def check_health(client: TestClient) -> None:
    payload = client.get("/api/health").json()
    print(f"health: status={payload['status']} db={payload['database']}")
    assert payload["database_exists"] is True
    assert payload["status"] in {"ok", "missing_key", "error"}


def check_config(client: TestClient) -> None:
    payload = client.get("/api/config").json()
    print(f"config: tables={len(payload['tables'])} model={payload['model']}")
    assert len(payload["tables"]) == 11
    assert payload["examples"]


def check_validation(client: TestClient) -> None:
    empty = client.post("/api/query", json={"question": "   "})
    print(f"空白问题 -> {empty.status_code} {empty.json().get('detail')}")
    assert empty.status_code == 400

    too_long = client.post("/api/query", json={"question": "x" * 2001})
    print(f"超长问题 -> {too_long.status_code}")
    assert too_long.status_code == 422

    stream_empty = client.post("/api/query/stream", json={"question": "  "})
    assert stream_empty.status_code == 400


def check_query_guard(client: TestClient) -> None:
    response = client.post("/api/query", json={"question": "有多少位艺术家？"})
    if server.settings.api_key:
        print("已配置 API Key，跳过查询守卫断言（避免真实调用模型）")
        return
    print(f"未配置 Key 的查询 -> {response.status_code} {response.json().get('detail')}")
    assert response.status_code == 400
    assert "OPENAI_API_KEY" in response.json()["detail"]


def check_progress_mapping() -> None:
    selection = TableSelection(tables=["Artist"], reason="测试", confidence=1.0)
    select_event = server._progress("select_tables", {"selection": selection, "tables": ["Artist"]})
    assert select_event["status"] == "done"
    assert "Artist" in select_event["detail"]

    failed_event = server._progress(
        "validate_execute",
        {"validation": ValidationResult(ok=False, message="no such column")},
    )
    assert failed_event["status"] == "error"

    ok_event = server._progress(
        "validate_execute",
        {"execution": ExecutionResult(columns=["n"], rows=[["1"]], row_count=1)},
    )
    assert ok_event["status"] == "done"
    assert "1 行" in ok_event["detail"]
    print("进度事件映射：通过")


class FakeGraph:
    """模拟 LangGraph 的 astream(stream_mode="updates")。"""

    async def astream(self, _state, config=None, stream_mode=None):
        yield {
            "select_tables": {
                "selection": TableSelection(tables=["Artist"], reason="测试", confidence=1.0),
                "tables": ["Artist"],
                "selection_feedback": "",
            }
        }
        yield {
            "finalize": {
                "output": WorkflowOutput(
                    question="测试问题",
                    answer="**测试答案**",
                    sql="SELECT 1",
                    tables=["Artist"],
                    row_count=1,
                )
            }
        }


class FakeWorkflow:
    async def run(self, question: str) -> WorkflowOutput:
        return WorkflowOutput(question=question, answer="非流式答案", row_count=1)


def check_stream_and_query() -> None:
    original = (server.GRAPH, server.WORKFLOW, server.settings)
    server.GRAPH = FakeGraph()
    server.WORKFLOW = FakeWorkflow()
    server.settings = dataclasses.replace(server.settings, api_key="test-key")
    try:
        with TestClient(server.app) as client:
            response = client.post("/api/query/stream", json={"question": "测试"})
            print(f"SSE 查询 -> {response.status_code}")
            assert response.status_code == 200
            events = [
                line.split(":", 1)[1].strip()
                for line in response.text.splitlines()
                if line.startswith("event:")
            ]
            print(f"SSE 事件：{events}")
            assert events == ["progress", "result", "done"]
            assert "选择相关表" in response.text
            assert '"answer": "**测试答案**"' in response.text

            plain = client.post("/api/query", json={"question": "测试"})
            print(f"非流式查询 -> {plain.status_code}")
            assert plain.status_code == 200
            assert plain.json()["output"]["answer"] == "非流式答案"
    finally:
        server.GRAPH, server.WORKFLOW, server.settings = original


def main() -> None:
    print(f"前端目录：{server.WEB_DIR}")
    print(f"后端目录：{server.BACKEND_DIR}")
    if server.STARTUP_ERROR:
        raise SystemExit(f"后端初始化失败：{server.STARTUP_ERROR}")

    with TestClient(server.app) as client:
        check_index(client)
        check_health(client)
        check_config(client)
        check_validation(client)
        check_query_guard(client)

    check_progress_mapping()
    check_stream_and_query()
    print("Web 冒烟测试通过。")


if __name__ == "__main__":
    main()
