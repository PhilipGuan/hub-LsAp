from app.config import ProviderConfig
from app.vision import (
    AnalysisError,
    normalize_result,
    parse_model_output,
)

PROVIDER = ProviderConfig(
    id="qwen",
    label="Qwen VL",
    model="qwen-vl-max-latest",
    base_url="https://example.invalid/v1",
    api_key="test",
    description="test",
)


def test_parse_fenced_json_with_surrounding_text():
    text = """研判结果如下：
```json
{"is_corridor": true, "risk_level": "中风险", "confidence": 0.82}
```
以上。"""
    assert parse_model_output(text)["risk_level"] == "中风险"


def test_parse_trailing_comma():
    data = parse_model_output('{"is_corridor": true, "risk_level": "低风险",}'
    )
    assert data["risk_level"] == "低风险"


def test_parse_rejects_plain_text():
    try:
        parse_model_output("楼道里有一个纸箱")
    except AnalysisError as exc:
        assert exc.status_code == 502
    else:
        raise AssertionError("expected AnalysisError")


def test_normalize_high_risk_result():
    result = normalize_result(
        {
            "is_corridor": True,
            "risk_level": "高风险",
            "confidence": 0.91,
            "summary": "楼道内停放电动车并飞线充电。",
            "hazards": [
                {
                    "category": "电动车停放",
                    "description": "电动车停在楼道口，旁边有充电线。",
                    "severity": "high",
                }
            ],
            "evidence": ["电动车占用通道", "墙面垂下充电线"],
            "suggestions": ["立即移走电动车并停止充电"],
            "need_action": True,
        },
        PROVIDER,
        1234,
    )
    assert result["risk_level"] == "高风险"
    assert result["hazards"][0]["severity"] == "high"
    assert result["elapsed_ms"] == 1234
    assert result["provider"] == "qwen"
    assert result["need_action"] is True


def test_normalize_non_corridor_overrides_risk_and_hazards():
    result = normalize_result(
        {
            "is_corridor": False,
            "risk_level": "高风险",
            "summary": "这是一张室内自拍。",
            "hazards": [{"description": "不存在的隐患"}],
        },
        PROVIDER,
        10,
    )
    assert result["risk_level"] == "非楼道"
    assert result["is_corridor"] is False
    assert result["hazards"] == []
    assert result["need_action"] is False


def test_normalize_confidence_percentage_and_aliases():
    result = normalize_result(
        {
            "is_corridor": "是",
            "risk_level": "中等风险",
            "confidence": 86,
            "summary": "通道有纸箱。",
        },
        PROVIDER,
        10,
    )
    assert result["confidence"] == 0.86
    assert result["risk_level"] == "中风险"


def test_normalize_infers_risk_from_hazards():
    result = normalize_result(
        {
            "is_corridor": True,
            "hazards": [
                {"category": "飞线充电", "description": "从窗户拉线充电", "severity": "high"}
            ],
            "summary": "窗外拉出充电线。",
        },
        PROVIDER,
        10,
    )
    assert result["risk_level"] == "高风险"


def test_normalize_accepts_string_hazards_and_falls_back_suggestions():
    result = normalize_result(
        {
            "is_corridor": True,
            "risk_level": "低风险",
            "hazards": ["门口放着一个纸箱"],
            "summary": "楼道口有一个纸箱。",
        },
        PROVIDER,
        10,
    )
    assert result["hazards"][0]["description"] == "门口放着一个纸箱"
    assert result["suggestions"]
    assert result["confidence"] == 0.6
