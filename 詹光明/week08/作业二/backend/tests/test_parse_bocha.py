"""博查响应解析的容错测试。

响应结构来自第三方文档交叉确认，未经官方一手确认；这些测试既锁住已确认的
结构，也覆盖「结构变了」时的降级行为。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.search import parse_datetime, parse_search_response, strip_html


def test_parses_real_fixture(bocha_ok: dict[str, Any]) -> None:
    """真实响应必须能解析出结果，且命中预期路径。"""
    results, matched_path = parse_search_response(bocha_ok)

    assert matched_path == "data.webPages.value"
    assert len(results) == 5

    first = results[0]
    assert first.title  # 标题非空
    assert first.url.startswith("http")
    assert first.site_name
    assert first.summary  # summary:true 时应有长摘要
    assert isinstance(first.published_at, datetime)


def test_snippet_and_summary_are_html_stripped() -> None:
    payload = {
        "data": {
            "webPages": {
                "value": [
                    {
                        "name": "<em>标题</em>",
                        "url": "https://example.com/a",
                        "snippet": "<b>粗体</b>描述",
                        "summary": "<p>段落</p>",
                    }
                ]
            }
        }
    }
    results, _ = parse_search_response(payload)

    assert results[0].title == "标题"
    assert results[0].snippet == "粗体描述"
    assert results[0].summary == "段落"


def test_missing_optional_fields_do_not_crash() -> None:
    """缺字段应当降级，而不是抛异常 —— 一个坏结果不该毁掉整轮检索。"""
    payload = {"data": {"webPages": {"value": [{"url": "https://example.com/a"}]}}}
    results, _ = parse_search_response(payload)

    assert len(results) == 1
    assert results[0].title == ""
    assert results[0].snippet == ""
    assert results[0].published_at is None


def test_alternate_value_paths() -> None:
    """结构假设失效时，仍应尝试其它可能的结果列表路径。"""
    item = {"title": "T", "url": "https://example.com/x"}

    for payload, expected in [
        ({"data": {"value": [item]}}, "data.value"),
        ({"data": {"results": [item]}}, "data.results"),
        ({"data": {"pages": [item]}}, "data.pages"),
        ({"webPages": {"value": [item]}}, "webPages.value"),
        ({"value": [item]}, "value"),
    ]:
        results, matched_path = parse_search_response(payload)
        assert matched_path == expected
        assert len(results) == 1


def test_alternate_field_names() -> None:
    """字段名可能有别名，别名也要能认出来。"""
    payload = {
        "data": {
            "webPages": {
                "value": [
                    {
                        "title": "标题",
                        "link": "https://example.com/a",
                        "description": "描述",
                        "site": "站点",
                        "pubDate": "2024-01-02",
                    }
                ]
            }
        }
    }
    results, _ = parse_search_response(payload)

    assert results[0].title == "标题"
    assert results[0].url == "https://example.com/a"
    assert results[0].snippet == "描述"
    assert results[0].site_name == "站点"
    assert results[0].published_at == datetime(2024, 1, 2)


def test_unknown_structure_reports_no_path() -> None:
    """完全不认识的结构要返回 None 路径，让调用方去打真实的顶层 key。"""
    results, matched_path = parse_search_response({"unexpected": {"shape": []}})

    assert results == []
    assert matched_path is None


def test_empty_result_list_still_reports_path() -> None:
    """路径命中但结果为空，与「路径没命中」是两回事，要能区分。"""
    results, matched_path = parse_search_response({"data": {"webPages": {"value": []}}})

    assert results == []
    assert matched_path == "data.webPages.value"


def test_items_without_url_or_title_are_dropped() -> None:
    """解析不出任何有用信息的条目直接丢掉。"""
    payload = {"data": {"webPages": {"value": [{"id": "x"}, {"url": "https://example.com/ok"}]}}}
    results, _ = parse_search_response(payload)

    assert len(results) == 1
    assert results[0].url == "https://example.com/ok"


class TestParseDatetime:
    def test_iso_with_offset(self) -> None:
        assert parse_datetime("2017-09-19T22:44:16+08:00") == datetime.fromisoformat(
            "2017-09-19T22:44:16+08:00"
        )

    def test_trailing_z(self) -> None:
        # 博查的 dateLastCrawled 带 Z 后缀（历史遗留），不应让它抛异常
        assert parse_datetime("2024-12-12T23:42:50Z") is not None

    def test_plain_date(self) -> None:
        assert parse_datetime("2024-01-02") == datetime(2024, 1, 2)

    def test_space_separated(self) -> None:
        assert parse_datetime("2024-01-02 03:04:05") == datetime(2024, 1, 2, 3, 4, 5)

    def test_garbage_returns_none(self) -> None:
        for bad in ("", "   ", "昨天", "not-a-date", None, 123):
            assert parse_datetime(bad) is None


class TestStripHtml:
    def test_removes_tags_and_entities(self) -> None:
        assert strip_html("<p>a&nbsp;b</p>&amp;c") == "a b&c"

    def test_empty_input(self) -> None:
        assert strip_html("") == ""
