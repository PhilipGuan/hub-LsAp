"""不依赖网络的单元测试：图片预处理、结果校验、演示模式。

运行： .venv/bin/python -m unittest discover -s tests -v
"""

from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path

# 将项目根目录加入到 sys.path 中，以便导入 analyzer 模块
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image  # noqa: E402

from analyzer import (  # noqa: E402
    RISK_LEVELS,
    AnalyzerError,
    _mock_result,
    normalize_result,
    prepare_image,
)

# 在内存中动态生成测试用的图片
def make_image(size=(3000, 2000), mode="RGB", color=(120, 140, 160)) -> bytes:
    img = Image.new(mode, size, color)
    buffer = io.BytesIO() # 在内存里创建图片文件，返回字节流
    img.save(buffer, format="PNG" if mode != "RGB" else "JPEG")
    return buffer.getvalue()

# 图片预处理测试
class PrepareImageTest(unittest.TestCase):
    # 大图压缩
    def test_downscales_and_converts_to_jpeg(self):
        data, mime = prepare_image(make_image())
        self.assertEqual(mime, "image/jpeg")
        self.assertEqual(data[:2], b"\xff\xd8")  # JPEG SOI
        with Image.open(io.BytesIO(data)) as img:
            self.assertLessEqual(max(img.size), 1280)
            self.assertEqual(img.mode, "RGB")

    # 小图保持大小
    def test_keeps_small_image_size(self):
        data, _ = prepare_image(make_image((640, 480)))
        with Image.open(io.BytesIO(data)) as img:
            self.assertEqual(img.size, (640, 480))

    # 透明通道处理：RGBA 图片转换为 RGB 图片，并添加白色背景
    def test_rgba_png_gets_white_background(self):
        data, _ = prepare_image(make_image((320, 240), mode="RGBA", color=(0, 0, 0, 0)))
        with Image.open(io.BytesIO(data)) as img:
            self.assertEqual(img.mode, "RGB")
            self.assertGreater(img.getpixel((10, 10))[0], 200)

    # 异常防御：非图片数据
    def test_rejects_non_image(self):
        with self.assertRaises(AnalyzerError) as ctx:
            prepare_image(b"not an image")
        self.assertEqual(ctx.exception.status_code, 400)

    # 异常防御：空数据
    def test_rejects_empty(self):
        with self.assertRaises(AnalyzerError) as ctx:
            prepare_image(b"")
        self.assertEqual(ctx.exception.status_code, 400)


# 结果校验与规整测试
class NormalizeResultTest(unittest.TestCase):
    # 接受规范的等级名称：正常的JSON，原样通过
    def test_accepts_canonical_level(self):
        result = normalize_result(
            {
                "risk_level": "高风险",
                "confidence": 0.91,
                "is_stairwell": True,
                "evidence": ["楼道停放电动车"],
                "suggestion": "立即移出楼道",
            }
        )
        self.assertEqual(result["risk_level"], "高风险")
        self.assertEqual(result["confidence"], 0.91)
        self.assertEqual(result["evidence"], ["楼道停放电动车"])

    # 别名映射
    def test_maps_aliases(self):
        self.assertEqual(
            normalize_result({"risk_level": "中等风险"})["risk_level"], "中风险"
        )
        self.assertEqual(normalize_result({"risk_level": "无隐患"})["risk_level"], "无风险")

    # 置信度百分数转换
    def test_confidence_percentage(self):
        self.assertEqual(normalize_result({"risk_level": "低风险", "confidence": 88})["confidence"], 0.88)
        self.assertEqual(normalize_result({"risk_level": "低风险", "confidence": "92%"})["confidence"], 0.92)

    # 分号切割
    def test_string_evidence_is_split(self):
        result = normalize_result({"risk_level": "低风险", "evidence": "少量纸箱；一把椅子"})
        self.assertEqual(result["evidence"], ["少量纸箱", "一把椅子"])

    # 业务逻辑强行覆盖：非楼道强制置为 NA
    def test_not_stairwell_forces_na(self):
        result = normalize_result({"risk_level": "高风险", "is_stairwell": False})
        self.assertEqual(result["risk_level"], "非楼道")
        self.assertFalse(result["is_stairwell"])

    # 非法等级值抛出异常
    def test_invalid_level_raises(self):
        with self.assertRaises(AnalyzerError):
            normalize_result({"risk_level": "极高"})

    # 所有等级值都覆盖
    def test_all_levels_are_covered(self):
        for level in RISK_LEVELS:
            result = normalize_result({"risk_level": level})
            self.assertEqual(result["risk_level"], level)


# 演示模式测试
class MockModeTest(unittest.TestCase):
    def test_deterministic_and_valid(self):
        payload = b"same-bytes"
        first = _mock_result(payload)
        second = _mock_result(payload)
        self.assertEqual(first, second)
        self.assertIn(first["risk_level"], RISK_LEVELS)
        self.assertTrue(first["mock"])
        self.assertGreaterEqual(first["confidence"], 0)
        self.assertLessEqual(first["confidence"], 1)


if __name__ == "__main__":
    unittest.main()
