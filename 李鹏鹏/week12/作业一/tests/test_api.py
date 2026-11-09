from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image

import app.main as main_module
from app.config import ProviderConfig


def png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (160, 120), (120, 90, 70)).save(buffer, format="PNG")
    return buffer.getvalue()


FAKE_PROVIDER = ProviderConfig(
    id="qwen",
    label="Qwen VL",
    model="mock-vl",
    base_url="http://example.invalid/v1",
    api_key="test",
    description="mock",
)

FAKE_RESULT = {
    "analysis_id": "abc123",
    "is_corridor": True,
    "risk_level": "中风险",
    "confidence": 0.88,
    "summary": "楼道内堆放纸箱，通道变窄。",
    "hazards": [
        {
            "category": "可燃堆物",
            "description": "右侧墙边堆放纸箱。",
            "severity": "medium",
        }
    ],
    "evidence": ["纸箱占用通道"],
    "suggestions": ["尽快清理纸箱"],
    "need_action": True,
    "provider": "qwen",
    "provider_label": "Qwen VL",
    "model": "mock-vl",
    "elapsed_ms": 1200,
    "timestamp": "2026-10-27T10:00:00+08:00",
}


def test_index_and_providers():
    client = TestClient(main_module.app)
    index = client.get("/")
    assert index.status_code == 200
    assert "楼道哨兵" in index.text

    providers = client.get("/api/providers")
    assert providers.status_code == 200
    assert providers.json()["providers"]


def test_analyze_with_mocked_model(monkeypatch):
    monkeypatch.setattr(main_module, "get_provider", lambda _: FAKE_PROVIDER)
    monkeypatch.setattr(main_module, "analyze_image", lambda *_: FAKE_RESULT)

    client = TestClient(main_module.app)
    response = client.post(
        "/api/analyze",
        files={"file": ("corridor.png", png_bytes(), "image/png")},
        data={"provider": "qwen"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["risk_level"] == "中风险"
    assert response.headers["x-analysis-id"] == "abc123"


def test_analyze_rejects_non_image_mime(monkeypatch):
    monkeypatch.setattr(main_module, "get_provider", lambda _: FAKE_PROVIDER)
    client = TestClient(main_module.app)
    response = client.post(
        "/api/analyze",
        files={"file": ("notes.txt", b"hello", "text/plain")},
        data={"provider": "qwen"},
    )
    assert response.status_code == 415


def test_analyze_rejects_corrupt_image(monkeypatch):
    monkeypatch.setattr(main_module, "get_provider", lambda _: FAKE_PROVIDER)
    client = TestClient(main_module.app)
    response = client.post(
        "/api/analyze",
        files={"file": ("corridor.jpg", b"not-an-image", "image/jpeg")},
        data={"provider": "qwen"},
    )
    assert response.status_code == 400


def test_analyze_rejects_unknown_provider():
    client = TestClient(main_module.app)
    response = client.post(
        "/api/analyze",
        files={"file": ("corridor.png", png_bytes(), "image/png")},
        data={"provider": "not-a-provider"},
    )
    assert response.status_code == 400
    assert "未知的模型标识" in response.json()["detail"]
