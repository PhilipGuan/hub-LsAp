"""接口集成测试：使用 FastAPI TestClient 验证路由契约。"""
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_health():
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_bert_api_single():
    resp = client.post("/v1/intent/bert", json={
        "request_id": "api-02",
        "request_text": "我的订单发货了吗",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["error_msg"] == "ok"
    assert body["classify_result"]
    for field in ("request_id", "request_text", "classify_result", "classify_time", "error_msg"):
        assert field in body


def test_tfidf_api_single():
    resp = client.post("/v1/intent/tfidf", json={
        "request_id": "api-03",
        "request_text": "我想退货怎么操作",
    })
    assert resp.status_code == 200
    assert resp.json()["error_msg"] == "ok"


def test_bert_api_batch():
    resp = client.post("/v1/intent/bert", json={
        "request_id": "api-04",
        "request_text": ["多少钱", "快递到哪了", "转人工"],
    })
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["classify_result"]) == 3


def test_missing_field_returns_422():
    resp = client.post("/v1/intent/bert", json={"request_id": "x"})
    assert resp.status_code == 422
