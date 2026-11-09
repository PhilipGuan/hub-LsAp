"""Bocha 搜索工具封装：一次查询 → 带摘要的来源列表。"""
from __future__ import annotations

import logging

import httpx

from .. import config
from ..models import Source

logger = logging.getLogger(__name__)


async def search(query: str, count: int | None = None) -> list[Source]:
    """执行一次 Web 搜索，返回带摘要的来源列表。

    参数：
        query: 检索关键词
        count: 返回条数，默认取 config.BOCHA_COUNT
    """
    count = count or config.BOCHA_COUNT
    headers = {
        "Authorization": f"Bearer {config.BOCHA_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {"query": query, "summary": True, "count": count}

    logger.info("检索：%s（count=%d）", query, count)
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(config.BOCHA_API_URL, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        logger.error("搜索请求失败：%s", exc)
        return []

    sources = _parse(data)
    logger.info("检索到 %d 个来源", len(sources))
    return sources


def _parse(data: dict) -> list[Source]:
    """解析 Bocha 返回结构 data.webPages.value。"""
    pages = data.get("data", {}).get("webPages", {}).get("value") or []
    sources: list[Source] = []
    for p in pages:
        if not isinstance(p, dict):
            continue
        sources.append(
            Source(
                url=p.get("url", ""),
                title=p.get("name", ""),
                site=p.get("siteName", ""),
                snippet=p.get("snippet", ""),
                content=p.get("summary", ""),
                published_at=p.get("datePublished", ""),
            )
        )
    return sources
