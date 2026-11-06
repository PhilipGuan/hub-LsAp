"""接口层测试：用演示模式避免真实调用模型。

运行： .venv/bin/python -m unittest discover -s tests -v
"""

from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

import main  # noqa: E402
from analyzer import RISK_LEVELS  # noqa: E402
from config import settings  # noqa: E402


# 在内存中动态生成测试用的图片
def jpeg_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (640, 480), (110, 130, 150)).save(buffer, format="JPEG")
    return buffer.getvalue()


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        self._mock = settings.mock_mode
        settings.mock_mode = True

    def tearDown(self):
        settings.mock_mode = self._mock

    def test_health(self):
        res = self.client.get("/api/health")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["mock"])

    def test_index_page(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("楼道消防安全隐患识别", res.text)

    def test_analyze_returns_structured_result(self):
        res = self.client.post(
            "/api/analyze",
            files={"file": ("stairwell.jpg", jpeg_bytes(), "image/jpeg")},
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertIn(body["risk_level"], RISK_LEVELS)
        self.assertIsInstance(body["evidence"], list)
        self.assertIsInstance(body["suggestion"], str)
        self.assertTrue(body["mock"])

    def test_rejects_non_image(self):
        res = self.client.post(
            "/api/analyze",
            files={"file": ("note.txt", b"hello", "text/plain")},
        )
        self.assertEqual(res.status_code, 415)

    def test_rejects_broken_image(self):
        res = self.client.post(
            "/api/analyze",
            files={"file": ("broken.jpg", b"not-an-image", "image/jpeg")},
        )
        self.assertEqual(res.status_code, 400)


if __name__ == "__main__":
    unittest.main()
