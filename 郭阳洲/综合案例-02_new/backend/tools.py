# -*- coding: utf-8 -*-
"""搜索工具：Bocha 网页搜索 API。

规格见 README.md / 任务说明书.md（T3）。
普通 async 函数（不是 function_tool），返回解析后的 list[dict]；
失败时直接抛异常，由调用方（engine）统一兜底为空结果。
"""

from __future__ import annotations

import logging

import httpx

from . import config

logger = logging.getLogger(__name__)

BOCHA_ENDPOINT = "https://api.bocha.cn/v1/web-search"
SNIPPET_MAX_LEN = 500  # 摘要截断长度


def _truncate(text: str, limit: int = SNIPPET_MAX_LEN) -> str:
    """截断过长文本，超出部分以省略号收尾。"""
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _parse_item(item: dict) -> dict:
    """把 Bocha 返回的单条结果映射成本项目的统一结构。"""
    snippet = item.get("snippet") or item.get("summary") or ""
    date = item.get("datePublished") or item.get("dateLastCrawled") or ""
    return {
        "title": item.get("name") or "",
        "url": item.get("url") or "",
        "snippet": _truncate(str(snippet)),
        "site_name": item.get("siteName") or "",
        "date": date,
    }


async def web_search(query: str) -> list[dict]:
    """调用 Bocha 网页搜索，返回统一结构的搜索结果列表。

    每条结果：{"title", "url", "snippet", "site_name", "date"}。
    网络 / 鉴权 / 响应结构异常一律抛出，交由调用方处理。
    """
    headers = {
        "Authorization": f"Bearer {config.BOCHA_API_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "query": query,
        "summary": True,
        "count": config.BOCHA_SEARCH_COUNT,
    }

    logger.info("Bocha 搜索开始: query=%r count=%d", query, config.BOCHA_SEARCH_COUNT)
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(BOCHA_ENDPOINT, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()

    raw_items = (data.get("data") or {}).get("webPages", {}).get("value") or []
    results = [_parse_item(item) for item in raw_items if isinstance(item, dict)]
    logger.info("Bocha 搜索完成: query=%r 返回 %d 条", query, len(results))
    return results


if __name__ == "__main__":
    import asyncio

    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    results = asyncio.run(web_search("天空为什么是蓝色的"))
    print(f"共 {len(results)} 条结果，展示前 3 条：")
    for i, r in enumerate(results[:3], 1):
        print(f"[{i}] {r['title']}")
        print(f"    url  = {r['url']}")
        print(f"    date = {r['date']}")
    assert results, "搜索结果不应为空"
    print("搜索工具自检 OK")
