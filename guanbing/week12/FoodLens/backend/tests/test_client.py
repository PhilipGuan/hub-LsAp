"""视觉客户端 JSON 解析的健壮性测试。"""

import pytest

from app.providers.client import VisionError, extract_json


def test_plain_object():
    assert extract_json('{"items": []}') == {"items": []}


def test_fenced_object():
    raw = '```json\n{"per_100g": {"kcal": 100}}\n```'
    assert extract_json(raw) == {"per_100g": {"kcal": 100}}


def test_trailing_extra_brace():
    """模型偶发多输出一个尾随 } 时仍能解析。"""
    raw = '{"items":[{"name_zh":"红烧肉","grams":180}]}}'
    assert extract_json(raw)["items"][0]["name_zh"] == "红烧肉"


def test_trailing_prose():
    raw = '{"items":[]}\n以上为识别结果，仅供参考试。'
    assert extract_json(raw) == {"items": []}


def test_no_json_raises():
    with pytest.raises(VisionError):
        extract_json("抱歉，我无法识别这张图片。")


def test_invalid_json_raises():
    with pytest.raises(VisionError):
        extract_json('{"items": [}')
