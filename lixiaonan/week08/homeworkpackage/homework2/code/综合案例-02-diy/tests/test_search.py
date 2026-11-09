"""S4 单测：博查响应解析与重试（不触网）。"""
import httpx
import pytest

from research.tools import search as search_mod
from research.tools.search import SearchError, parse_response, web_search

BOCHA_RESPONSE = {
    "code": 200,
    "data": {
        "webPages": {
            "value": [
                {
                    "name": "标题A",
                    "url": "https://a.com/x?utm_source=weixin",
                    "summary": "摘要A",
                    "datePublished": "2026-08-01T00:00:00Z",
                },
                {"name": "标题B", "url": "https://b.com/", "snippet": "只有 snippet"},
                {"name": "无URL", "url": ""},
            ]
        }
    },
}


def test_parse_response():
    hits = parse_response(BOCHA_RESPONSE)
    assert len(hits) == 2
    assert hits[0].title == "标题A"
    assert hits[0].summary == "摘要A"
    assert hits[0].date_published.startswith("2026-08-01")
    assert hits[1].summary == "只有 snippet"


def test_web_search_retries_then_succeeds(monkeypatch):
    calls = {"n": 0}

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return BOCHA_RESPONSE

    def fake_post(url, **kwargs):
        calls["n"] += 1
        if calls["n"] < 2:
            raise httpx.ConnectError("boom")
        return FakeResp()

    monkeypatch.setattr(search_mod.httpx, "post", fake_post)
    monkeypatch.setattr(search_mod.time, "sleep", lambda s: None)
    monkeypatch.setattr(search_mod.config, "BOCHA_API_KEY", "test-key")

    hits = web_search("测试")
    assert calls["n"] == 2
    assert len(hits) == 2


def test_web_search_raises_after_retries(monkeypatch):
    def fake_post(url, **kwargs):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(search_mod.httpx, "post", fake_post)
    monkeypatch.setattr(search_mod.time, "sleep", lambda s: None)
    monkeypatch.setattr(search_mod.config, "BOCHA_API_KEY", "test-key")

    with pytest.raises(SearchError):
        web_search("测试")


def test_web_search_requires_key(monkeypatch):
    monkeypatch.setattr(search_mod.config, "BOCHA_API_KEY", "")
    with pytest.raises(SearchError, match="BOCHA_API_KEY"):
        web_search("测试")
