"""博查 Web Search 封装（S4）。"""
from __future__ import annotations

import time

import httpx

from .. import config
from ..models import SearchHit


class SearchError(RuntimeError):
    """搜索失败（重试后仍不成功）。调用方捕获后跳过，不中断整轮。"""


def web_search(query: str, count: int | None = None, timeout: float = 15.0, retries: int = 2) -> list[SearchHit]:
    """调用博查搜索，返回 SearchHit 列表；失败抛 SearchError。"""
    if not config.BOCHA_API_KEY:
        raise SearchError("BOCHA_API_KEY 未配置（请在 .env 中设置）")
    payload = {"query": query, "summary": True, "count": count or config.SEARCH_COUNT}
    headers = {"Authorization": f"Bearer {config.BOCHA_API_KEY}", "Content-Type": "application/json"}
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            resp = httpx.post(config.SEARCH_URL, json=payload, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return parse_response(resp.json())
        except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError, ValueError) as e:
            last_err = e
            if attempt < retries:
                time.sleep(1.5 * attempt)  # 简单退避
    raise SearchError(f"搜索 '{query}' 失败：{last_err}")


def parse_response(data: dict) -> list[SearchHit]:
    """解析博查响应（Bing 风格 data.webPages.value 结构）。"""
    hits: list[SearchHit] = []
    pages = (((data or {}).get("data") or {}).get("webPages") or {}).get("value") or []
    for item in pages:
        url = item.get("url") or ""
        if not url:
            continue
        hits.append(
            SearchHit(
                url=url,
                title=item.get("name") or url,
                summary=item.get("summary") or item.get("snippet") or "",
                date_published=item.get("datePublished") or "",
            )
        )
    return hits
