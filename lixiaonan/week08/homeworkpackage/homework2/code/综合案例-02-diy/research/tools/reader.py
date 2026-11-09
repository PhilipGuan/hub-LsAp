"""网页阅读（P0：博查 summary；P1 可扩展为抓取全文）。"""
from __future__ import annotations

from ..models import SearchHit

MAX_MATERIAL_CHARS = 4000


def read_material(hit: SearchHit) -> str:
    """返回一个页面的"阅读材料"。

    P0：直接使用博查返回的网页摘要（parse_response 已用 snippet 兜底）。
    P1：可改为 httpx 抓取 URL + trafilatura 抽取正文，再截断到 MAX_MATERIAL_CHARS。
    """
    return (hit.summary or "").strip()[:MAX_MATERIAL_CHARS]
