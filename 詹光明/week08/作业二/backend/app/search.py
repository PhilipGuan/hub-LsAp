"""博查 Web Search 客户端。

响应结构未经官方一手确认（官方文档域名访问不通），所以解析层做多路径、
多字段名的容错，并在解析出 0 条时把实际顶层 key 记下来供核对。

设计原则：**单个关键词检索失败只记录，不抛异常** —— 一次 429 不该让整个
研究中断。
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from datetime import datetime
from typing import Any

import httpx

from .config import Settings
from .config import settings as default_settings
from .models import RawSearchResult, SearchOutcome

logger = logging.getLogger(__name__)

# 老式 UA 会被部分站点拒绝，这里用一个常见的桌面 Chrome
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# 结果列表可能出现的 JSON 路径，按优先级尝试
_VALUE_PATHS: tuple[tuple[str, ...], ...] = (
    ("data", "webPages", "value"),
    ("data", "webPages", "values"),
    ("data", "value"),
    ("data", "results"),
    ("data", "pages"),
    ("data", "items"),
    ("webPages", "value"),
    ("value",),
    ("results",),
)

# 归一化字段名 -> 可能的原始字段名
_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "title": ("name", "title", "headline"),
    "url": ("url", "link", "href"),
    "display_url": ("displayUrl", "display_url", "displayLink"),
    "snippet": ("snippet", "description", "desc"),
    "summary": ("summary", "longDescription", "long_description"),
    "site_name": ("siteName", "site_name", "site", "source"),
    "published_at": ("datePublished", "date_published", "publishedAt", "pubDate"),
    "date_last_crawled": ("dateLastCrawled", "date_last_crawled", "lastCrawled"),
}

_TAG_RE = re.compile(r"<[^>]+>")

# 重试的退避序列（秒），实际会叠加抖动
_BACKOFF_S = (1.0, 3.0, 8.0)
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

# 视为成功的业务状态码（该字段的确切取值未确认，放宽处理）
_SUCCESS_CODES = frozenset({0, 200})


def strip_html(text: str) -> str:
    """博查的 summary 字段可能带 HTML 标签，统一剥掉。"""
    if not text:
        return ""
    return _TAG_RE.sub("", text).replace("&nbsp;", " ").replace("&amp;", "&").strip()


def parse_datetime(value: Any) -> datetime | None:
    """尽量解析博查的日期字段；解析不了就返回 None，不因一个日期炸掉整轮检索。"""
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text:
        return None
    # 末尾的 Z 在博查这里是历史遗留（实为 UTC+8），按 UTC 解析仅影响时区偏移
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _first_value(item: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in item and item[key] not in (None, ""):
            return item[key]
    return None


def _field(item: dict[str, Any], name: str) -> Any:
    """按字段的归一化名取原始值，依次尝试它所有的别名。"""
    return _first_value(item, *_FIELD_ALIASES[name])


def _extract_items(payload: Any) -> tuple[list[dict[str, Any]], str | None]:
    """按优先级在 payload 里找结果列表，返回 (列表, 命中的路径)。"""
    for path in _VALUE_PATHS:
        node: Any = payload
        for key in path:
            if not isinstance(node, dict) or key not in node:
                node = None
                break
            node = node[key]
        if isinstance(node, list) and node:
            return [x for x in node if isinstance(x, dict)], ".".join(path)
        if isinstance(node, list):  # 命中路径但列表为空，仍然算命中
            return [], ".".join(path)
    return [], None


def parse_search_response(payload: dict[str, Any]) -> tuple[list[RawSearchResult], str | None]:
    """把博查的原始响应解析成归一化的结果列表。

    返回 ``(results, matched_path)``；``matched_path`` 为 None 说明没找到结果列表，
    调用方据此打印实际的顶层 key 以便核对结构。
    """
    items, matched_path = _extract_items(payload)
    if matched_path is None:
        return [], None

    results: list[RawSearchResult] = []
    for item in items:
        url = str(_field(item, "url") or "").strip()
        title = strip_html(str(_field(item, "title") or ""))
        if not url and not title:
            continue

        results.append(
            RawSearchResult(
                title=title,
                url=url,
                display_url=str(_field(item, "display_url") or "").strip(),
                snippet=strip_html(str(_field(item, "snippet") or ""))[:500],
                summary=strip_html(str(_field(item, "summary") or "")),
                site_name=strip_html(str(_field(item, "site_name") or "")),
                published_at=parse_datetime(_field(item, "published_at")),
                date_last_crawled=parse_datetime(_field(item, "date_last_crawled")),
            )
        )
    return results, matched_path


class _RateLimiter:
    """全局最小间隔限流器：两次请求之间至少间隔 ``min_interval`` 秒。"""

    def __init__(self, min_interval: float) -> None:
        self._min_interval = min_interval
        self._lock = asyncio.Lock()
        self._last_request = 0.0

    async def acquire(self) -> None:
        async with self._lock:
            elapsed = time.monotonic() - self._last_request
            wait = self._min_interval - elapsed
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = time.monotonic()


class BochaSearchClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or default_settings
        self._limiter = _RateLimiter(self._settings.bocha_min_interval_s)
        self._client: httpx.AsyncClient | None = None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._settings.bocha_timeout_s,
                headers={
                    "Authorization": f"Bearer {self._settings.bocha_api_key}",
                    "Content-Type": "application/json",
                    "User-Agent": _USER_AGENT,
                },
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> BochaSearchClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def search(
        self,
        query: str,
        *,
        count: int | None = None,
        summary: bool = True,
        freshness: str | None = None,
        keep_raw: bool = False,
    ) -> SearchOutcome:
        """执行一次检索。**任何失败都以 ``ok=False`` 返回，不抛异常。**

        ``keep_raw=True`` 时把原始 JSON 一并带回（供 CLI 核对字段，平时不必开）。
        """
        if not self._settings.bocha_api_key:
            return SearchOutcome(
                query=query, ok=False, error="BOCHA_API_KEY 未配置（请在 .env 里填写）"
            )

        body = {
            "query": query,
            "summary": summary,
            "count": count or self._settings.search_count_per_query,
            "freshness": freshness or self._settings.freshness,
        }

        last_error = ""
        for attempt in range(1, self._settings.bocha_max_retries + 1):
            await self._limiter.acquire()
            started = time.monotonic()
            try:
                client = await self._http()
                response = await client.post(self._settings.bocha_base_url, json=body)
            except httpx.TimeoutException:
                last_error = "请求超时"
            except httpx.HTTPError as exc:
                last_error = f"网络错误: {exc.__class__.__name__}: {exc}"
            else:
                latency_ms = int((time.monotonic() - started) * 1000)

                retryable = response.status_code in _RETRYABLE_STATUS
                if retryable and attempt < self._settings.bocha_max_retries:
                    last_error = f"HTTP {response.status_code}"
                    logger.warning(
                        "博查检索返回 %s，第 %d 次重试: %s", response.status_code, attempt, query
                    )
                    await self._sleep_backoff(attempt)
                    continue

                return self._handle_response(query, response, latency_ms, attempt, keep_raw)

            logger.warning("博查检索失败（第 %d 次）: %s | %s", attempt, query, last_error)
            if attempt < self._settings.bocha_max_retries:
                await self._sleep_backoff(attempt)

        return SearchOutcome(
            query=query, ok=False, attempts=self._settings.bocha_max_retries, error=last_error
        )

    async def _sleep_backoff(self, attempt: int) -> None:
        base = _BACKOFF_S[min(attempt - 1, len(_BACKOFF_S) - 1)]
        await asyncio.sleep(base + random.uniform(0, 0.5))  # noqa: S311 - 抖动，非安全用途

    def _handle_response(
        self,
        query: str,
        response: httpx.Response,
        latency_ms: int,
        attempt: int,
        keep_raw: bool = False,
    ) -> SearchOutcome:
        if response.status_code != 200:
            return SearchOutcome(
                query=query,
                ok=False,
                attempts=attempt,
                latency_ms=latency_ms,
                error=f"HTTP {response.status_code}: {response.text[:200]}",
            )

        try:
            payload = response.json()
        except ValueError:
            return SearchOutcome(
                query=query,
                ok=False,
                attempts=attempt,
                latency_ms=latency_ms,
                error=f"响应不是合法 JSON: {response.text[:200]}",
            )

        if not isinstance(payload, dict):
            return SearchOutcome(
                query=query,
                ok=False,
                attempts=attempt,
                latency_ms=latency_ms,
                error="响应顶层不是对象",
            )

        code = payload.get("code")
        msg = payload.get("msg")
        results, matched_path = parse_search_response(payload)

        if matched_path is None:
            # 关键诊断：结构假设不成立时，把真实结构打出来
            logger.warning(
                "博查响应里没找到结果列表，实际顶层 key: %s",
                sorted(payload.keys()),
            )
            return SearchOutcome(
                query=query,
                ok=False,
                attempts=attempt,
                latency_ms=latency_ms,
                code=code if isinstance(code, int) else None,
                msg=msg if isinstance(msg, str) else None,
                top_level_keys=sorted(payload.keys()),
                error="响应中未找到结果列表（结构假设可能已失效，请核对）",
                raw=payload if keep_raw else None,
            )

        ok = code is None or code in _SUCCESS_CODES
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        web_pages = data.get("webPages") if isinstance(data.get("webPages"), dict) else {}

        return SearchOutcome(
            query=query,
            ok=ok,
            results=results,
            matched_path=matched_path,
            code=code if isinstance(code, int) else None,
            msg=msg if isinstance(msg, str) else None,
            log_id=payload.get("log_id") if isinstance(payload.get("log_id"), str) else None,
            total_estimated_matches=web_pages.get("totalEstimatedMatches"),
            latency_ms=latency_ms,
            attempts=attempt,
            error=None if ok else f"业务状态码 {code}: {msg}",
            raw=payload if keep_raw else None,
        )


__all__ = [
    "BochaSearchClient",
    "parse_datetime",
    "parse_search_response",
    "strip_html",
]
