from __future__ import annotations

from sql_agent.contracts import (
    Claim,
    EmptyClassification,
    allowed_values,
    check_sanity,
    classify_empty,
    extract_numbers,
    fill_time_series,
    template_answer,
    verify_answer_text,
    verify_claims,
)


def test_extract_numbers_ignores_dates_and_product_codes() -> None:
    text = "数据截止 2013-12-22，MPEG-4 音频共 12 首，占比 3.5%。"
    numbers = extract_numbers(text)
    assert 2013 not in numbers
    assert -4 not in numbers
    assert 12 in numbers
    assert 3.5 in numbers


def test_verify_answer_text() -> None:
    allowed = [35.64, 412]
    assert verify_answer_text("销售额为 35.64 美元，共 412 张。", allowed) == []
    unmatched = verify_answer_text("销售额为 99.99 美元。", allowed)
    assert unmatched == [99.99]


def test_verify_answer_text_skips_years_and_small_ints() -> None:
    assert verify_answer_text("2010 年共有 5 个国家。", []) == []


def test_verify_claims_tolerance() -> None:
    claims = [Claim("a", 35.64), Claim("b", 35.65), Claim("c", 99.0)]
    matched, unmatched = verify_claims(claims, [35.64])
    assert [claim.text for claim in matched] == ["a", "b"]
    assert unmatched == [99.0]


def test_allowed_values_includes_totals_and_percentages() -> None:
    columns = ["country", "revenue"]
    rows = [("USA", 100.0), ("Germany", 50.0)]
    values = allowed_values(columns, rows)
    assert 100.0 in values
    assert 150.0 in values
    assert any(abs(v - 66.6667) < 0.01 for v in values)


def test_classify_empty_out_of_range() -> None:
    empty = classify_empty([], [], out_of_range=True, data_until="2013-12-22")
    assert empty is not None
    assert empty.reason == "out_of_range"
    assert "2013-12-22" in empty.message


def test_classify_empty_true_zero() -> None:
    empty = classify_empty(["n"], [(0,)], out_of_range=False, data_until="2013-12-22")
    assert empty is not None
    assert empty.reason == "true_zero"


def test_classify_empty_probe() -> None:
    empty = classify_empty(
        ["n"], [], out_of_range=False, data_until="2013-12-22", probe=lambda: True
    )
    assert empty is not None
    assert empty.reason == "conditions_too_strict"


def test_classify_empty_unknown() -> None:
    empty = classify_empty(
        ["n"], [], out_of_range=False, data_until="2013-12-22", probe=lambda: False
    )
    assert empty is not None
    assert empty.reason == "unknown"
    assert classify_empty(["n"], [(1,)], out_of_range=False, data_until="x") is None


def test_fill_time_series() -> None:
    filled = fill_time_series(
        ["month", "revenue"],
        [("2013-01", 10.0), ("2013-03", 30.0)],
        start="2013-01-01",
        end="2013-03-31",
        granularity="month",
    )
    assert [row[0] for row in filled.rows] == ["2013-01", "2013-02", "2013-03"]
    assert filled.rows[1] == ("2013-02", 0)
    assert filled.filled == 1
    assert filled.assumption


def test_fill_time_series_ignores_other_granularity() -> None:
    result = fill_time_series(
        ["month", "revenue"],
        [("2013-01", 10.0)],
        start="2013-01-01",
        end="2013-02-28",
        granularity="quarter",
    )
    assert result.filled == 0


def test_check_sanity_negative_and_duplicate() -> None:
    issues = check_sanity(["revenue"], [(-1.0,)])
    assert any(issue.code == "E7008" for issue in issues)
    duplicate = check_sanity(["country", "revenue"], [("USA", 1.0), ("USA", 1.0)])
    assert any("fan-out" in issue.detail for issue in duplicate)


def test_check_sanity_count_exceeds_base_rows() -> None:
    issues = check_sanity(["customer_count"], [(500,)], base_row_count=100)
    assert any(issue.severity == "error" for issue in issues)


def test_template_answer_single_value() -> None:
    text = template_answer(
        columns=["revenue"],
        rows=[(35.64,)],
        row_count=1,
        truncated=False,
        metric={"label": "销售额", "unit": "USD"},
        data_until="2013-12-22",
        assumptions=["按账单粒度"],
    )
    assert "35.64" in text
    assert "USD" in text
    assert "2013-12-22" in text


def test_template_answer_empty() -> None:
    empty = EmptyClassification("out_of_range", "数据未覆盖该时间范围。")
    text = template_answer(
        columns=[],
        rows=[],
        row_count=0,
        truncated=False,
        metric=None,
        data_until="2013-12-22",
        assumptions=[],
        empty=empty,
    )
    assert text == empty.message
