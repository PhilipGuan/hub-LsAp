"""搜索工具：真实模式调用 Bocha，mock 模式生成本地演示数据。"""
from __future__ import annotations

import hashlib

import httpx

from . import config


async def web_search(query: str) -> list[dict]:
    if config.mock_mode() or not config.BOCHA_API_KEY:
        return _mock_search(query)

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "https://api.bocha.cn/v1/web-search",
            headers={
                "Authorization": f"Bearer {config.BOCHA_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "query": query,
                "summary": True,
                "count": config.BOCHA_SEARCH_COUNT,
            },
        )
        response.raise_for_status()
        payload = response.json()

    pages = payload.get("data", {}).get("webPages", {}).get("value", [])
    results: list[dict] = []
    for page in pages[: config.BOCHA_SEARCH_COUNT]:
        results.append(
            {
                "title": page.get("name") or f"{query} 相关资料",
                "url": page.get("url") or "",
                "snippet": (page.get("snippet") or page.get("summary") or "")[:600],
                "site_name": page.get("siteName") or "",
                "date": page.get("datePublished") or page.get("dateLastCrawled") or "",
            }
        )
    return results


def _mock_search(query: str) -> list[dict]:
    seed = int(hashlib.sha1(query.encode("utf-8")).hexdigest()[:8], 16)
    topics = [
        ("概念与背景", "介绍该主题的基本定义、发展背景和主要问题。"),
        ("产品与工具", "梳理代表性产品、工具及其典型使用场景。"),
        ("对比与差异", "比较不同方案的定位、优势和限制。"),
        ("趋势与结论", "总结近期趋势、争议点和值得继续关注的方面。"),
    ]
    results: list[dict] = []
    for index, (suffix, summary) in enumerate(topics[: config.BOCHA_SEARCH_COUNT], 1):
        token = f"{(seed + index):08x}"
        results.append(
            {
                "title": f"{query}：{suffix}",
                "url": f"mock://research/{token}",
                "snippet": f"{summary} 这是主题“{query}”的演示搜索结果第 {index} 条。",
                "site_name": "Mock Research",
                "date": config.today_str(),
            }
        )
    return results
