"""搜索工具：Bocha 网页搜索。

`web_search` 为普通 async 函数，返回解析后的 `list[dict]`
（字段：title / url / summary / site_name / date），供 engine 直接函数调用。
"""
import logging

import httpx

from .config import settings

logger = logging.getLogger(__name__)


def _normalize(data) -> list[dict]:
    """把 Bocha 响应解析为统一结构的 list[dict]；对字段名做多种兜底。"""
    results: list[dict] = []
    if isinstance(data, dict):
        node = data.get("data", data)
    else:
        node = data

    if isinstance(node, dict):
        items = (
            node.get("web_results")
            or node.get("results")
            or node.get("items")
            or []
        )
    elif isinstance(node, list):
        items = node
    else:
        items = []

    for it in items:
        if not isinstance(it, dict):
            continue
        if not (it.get("url") or it.get("link")):
            continue
        results.append({
            "title": it.get("title") or it.get("name") or "",
            "url": it.get("url") or it.get("link") or "",
            "summary": (
                it.get("summary")
                or it.get("snippet")
                or it.get("desc")
                or it.get("description")
                or ""
            ),
            "site_name": it.get("site_name") or it.get("siteName") or it.get("source") or "",
            "date": str(
                it.get("date")
                or it.get("publish_date")
                or it.get("date_info")
                or ""
            ),
        })
    return results


async def web_search(query: str, summary: bool = True, count: int | None = None) -> list[dict]:
    """对 Bocha web-search 端点发起一次检索，返回解析后的 list[dict]。"""
    count = count or settings.bocha_default_count
    if not settings.bocha_api_key:
        raise RuntimeError("未配置 BOCHA_API_KEY（.env），无法进行 Web 检索")

    headers = {
        "Authorization": f"Bearer {settings.bocha_api_key}",
        "Content-Type": "application/json",
    }
    payload = {"query": query, "summary": summary, "count": count}

    logger.info("Bocha 检索 query=%r count=%d", query, count)
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(settings.bocha_endpoint, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

    parsed = _normalize(data)
    logger.info("Bocha 返回 %d 条结果 query=%r", len(parsed), query)
    return parsed


if __name__ == "__main__":
    import asyncio
    import logging

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    async def _demo():
        topic = "天空为什么是蓝色的"
        print("=== Bocha 单次搜索 demo ===")
        items = await web_search(topic, summary=True)
        for i, it in enumerate(items, 1):
            print(f"[{i}] {it.get('title')}")
            print(f"    url   : {it.get('url')}")
            print(f"    site  : {it.get('site_name')}  date={it.get('date')}")
            print(f"    summary: {(it.get('summary') or '')[:80]}")

    if not settings.bocha_api_key:
        print("未配置 BOCHA_API_KEY（.env），跳过真实检索 demo。")
    else:
        asyncio.run(_demo())