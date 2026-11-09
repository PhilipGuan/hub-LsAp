"""外部工具：Bocha 网页搜索 + 网页正文抓取。"""
from __future__ import annotations

import logging
import re

import httpx

from backend import config
from backend.models import SearchResult

logger = logging.getLogger(__name__)

_TAG = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
_HTML = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f\v]+")
_BLANK = re.compile(r"\n{3,}")


def bocha_search(query: str, count: int | None = None, freshness: str = "") -> list[SearchResult]:
    """调用 Bocha 网页搜索，返回结构化结果。失败返回空列表，不抛异常。"""
    if not config.BOCHA_API_KEY:
        logger.error("未配置 BOCHA_API_KEY")
        return []
    body: dict = {
        "query": query,
        "summary": True,
        "count": count or config.SEARCH_COUNT,
    }
    if freshness:
        body["freshness"] = freshness
    try:
        with httpx.Client(timeout=config.SEARCH_TIMEOUT) as cli:
            resp = cli.post(
                config.BOCHA_SEARCH_URL,
                headers={
                    "Authorization": f"Bearer {config.BOCHA_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Bocha 搜索失败（query=%s）：%s", query, exc)
        return []

    payload = (data.get("data") or {}) if isinstance(data, dict) else {}
    web_pages = payload.get("webPages") or {}
    raw_list = web_pages.get("value") or []
    results: list[SearchResult] = []
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        url = item.get("url") or ""
        if not url:
            continue
        results.append(
            SearchResult(
                title=item.get("name") or "",
                url=url,
                snippet=item.get("snippet") or "",
                summary=item.get("summary") or "",
                site_name=item.get("siteName") or "",
                publish_time=item.get("dateLastCrawled") or item.get("datePublished") or "",
            )
        )
    logger.info("Bocha 搜索「%s」返回 %s 条", query, len(results))
    return results


def fetch_page(url: str, max_chars: int | None = None) -> str:
    """抓取网页正文（纯文本，截断到 max_chars）。失败返回空串。"""
    limit = max_chars or config.MAX_FETCH_CHARS
    try:
        with httpx.Client(timeout=config.FETCH_TIMEOUT, follow_redirects=True) as cli:
            resp = cli.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; DeepResearch/1.0)"},
            )
            resp.raise_for_status()
            html = resp.text
    except Exception as exc:  # noqa: BLE001
        logger.warning("抓取失败（%s）：%s", url, exc)
        return ""

    text = _TAG.sub(" ", html)
    text = _HTML.sub("\n", text)
    text = _WS.sub(" ", text)
    text = _BLANK.sub("\n\n", text)
    return text.strip()[:limit]


def dedup(results: list[SearchResult]) -> list[SearchResult]:
    """按 URL 去重，保持顺序。"""
    seen: set[str] = set()
    out: list[SearchResult] = []
    for r in results:
        key = r.url.rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


if __name__ == "__main__":
    from backend.config import setup_logging

    setup_logging()
    qs = "天空为什么是蓝色的"
    print(f"搜索：{qs}")
    rs = bocha_search(qs, count=3)
    for i, r in enumerate(rs, 1):
        print(f"\n[{i}] {r.title}")
        print("    url:", r.url)
        print("    site:", r.site_name)
        print("    摘要:", (r.text or "")[:120].replace("\n", " "))
    print("\n去重测试：", len(dedup(rs + rs)), "条（原始", len(rs) + len(rs), "条）")
    if rs:
        txt = fetch_page(rs[0].url, max_chars=500)
        print("\n抓页测试（前 300 字）：")
        print(txt[:300])
