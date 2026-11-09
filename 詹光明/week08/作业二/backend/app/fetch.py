"""网页抓取。

三条原则：
1. **永不抛出** —— 所有异常都写进 ``Source.fetch_status``，一次抓取失败不该中断研究。
2. **域名级礼貌** —— 同一域名串行 + 最小间隔，别把人家站点打挂。
3. **失败要显式** —— 抓不到就如实记录状态，然后降级到搜索摘要，不假装读过了。
"""

from __future__ import annotations

import asyncio
import logging
import time
from urllib.parse import urlsplit

import httpx

from .config import Settings
from .config import settings as default_settings
from .dedup import content_hash, normalize_url, registrable_domain
from .extract import decode_html, extract_content
from .models import ContentOrigin, FetchStatus, Source

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# 只处理这些类型；PDF 之类直接跳过
_HTML_TYPES = ("text/html", "application/xhtml+xml", "text/plain")


class _DomainGovernor:
    """按域名串行 + 最小间隔，避免对同一站点并发轰炸。"""

    def __init__(self, min_interval: float) -> None:
        self._min_interval = min_interval
        self._locks: dict[str, asyncio.Lock] = {}
        self._last: dict[str, float] = {}

    async def acquire(self, domain: str) -> None:
        lock = self._locks.setdefault(domain, asyncio.Lock())
        await lock.acquire()
        elapsed = time.monotonic() - self._last.get(domain, 0.0)
        wait = self._min_interval - elapsed
        if wait > 0:
            await asyncio.sleep(wait)

    def release(self, domain: str) -> None:
        self._last[domain] = time.monotonic()
        lock = self._locks.get(domain)
        if lock is not None and lock.locked():
            lock.release()


class Fetcher:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or default_settings
        self._governor = _DomainGovernor(self._settings.fetch_domain_interval_s)
        self._semaphore = asyncio.Semaphore(self._settings.fetch_max_concurrency)
        self._client: httpx.AsyncClient | None = None
        self._blocked = tuple(d.lower() for d in self._settings.blocked_domains)

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                follow_redirects=True,
                timeout=self._settings.fetch_timeout_s,
                headers={
                    "User-Agent": _USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                },
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> Fetcher:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    def _is_blocked(self, url: str) -> bool:
        host = (urlsplit(url).hostname or "").lower()
        return any(host == d or host.endswith("." + d) for d in self._blocked)

    async def fetch_many(self, sources: list[Source]) -> list[Source]:
        """并发抓取一批来源，就地更新并返回。"""
        if not self._settings.fetch_enabled or not sources:
            return sources
        await asyncio.gather(*(self.fetch_one(s) for s in sources))
        return sources

    async def fetch_one(self, source: Source) -> Source:
        """抓取单个来源。**任何失败都只写状态，不抛异常。**"""
        source.fetched = True

        if self._is_blocked(source.url):
            source.fetch_status = FetchStatus.blocked
            source.fetch_error = "在反爬黑名单中，改用搜索摘要"
            self._apply_fallback(source)
            return source

        domain = registrable_domain(source.url) or (urlsplit(source.url).hostname or "")

        try:
            await self._semaphore.acquire()
            try:
                await self._governor.acquire(domain)
                try:
                    raw, content_type, status = await self._download(source.url)
                finally:
                    self._governor.release(domain)
            finally:
                self._semaphore.release()
        except httpx.TimeoutException:
            source.fetch_status = FetchStatus.timeout
            source.fetch_error = "抓取超时"
        except httpx.HTTPError as exc:
            source.fetch_status = FetchStatus.connection_error
            source.fetch_error = f"{exc.__class__.__name__}: {exc}"
        except Exception as exc:  # noqa: BLE001 - 兜底，绝不让抓取异常逃逸
            source.fetch_status = FetchStatus.connection_error
            source.fetch_error = f"未预期错误: {exc.__class__.__name__}: {exc}"
        else:
            source.http_status = status
            if status != 200:
                source.fetch_status = FetchStatus.http_error
                source.fetch_error = f"HTTP {status}"
            else:
                self._apply_html(source, raw, content_type)
                # 页面下下来了但一条正文都没抽出来（模板页 / 纯 JS 渲染页）。
                # 这种必须如实标成 extract_failed —— 标成 ok 会让置信度的
                # fetch_success 因子把「什么都没读到」算成成功。
                if source.content_origin is ContentOrigin.page:
                    source.fetch_status = FetchStatus.ok
                else:
                    source.fetch_status = FetchStatus.extract_failed
                    source.fetch_error = (
                        f"HTTP 200 但正文提取失败，降级为 {source.content_origin.value}"
                    )

        if source.fetch_status is not FetchStatus.ok:
            self._apply_fallback(source)

        return source

    async def _download(self, url: str) -> tuple[bytes, str, int]:
        """流式下载并在超过体积上限时截断，返回 (原始字节, content-type, 状态码)。

        这里故意返回字节而不是解码后的文本 —— 编码要等看到 HTML 头部的
        ``<meta charset>`` 才能确定，见 ``extract.decode_html``。
        """
        client = await self._http()
        async with client.stream("GET", url) as response:
            if response.status_code != 200:
                return b"", "", response.status_code

            content_type = response.headers.get("content-type", "").lower()
            if content_type and not any(t in content_type for t in _HTML_TYPES):
                raise _UnsupportedTypeError(content_type)

            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                chunks.append(chunk)
                size += len(chunk)
                if size >= self._settings.fetch_max_bytes:
                    logger.debug("响应超过 %d 字节，截断: %s", self._settings.fetch_max_bytes, url)
                    break

            return b"".join(chunks), content_type, response.status_code

    def _apply_html(self, source: Source, raw: bytes, content_type: str) -> None:
        html = decode_html(raw, content_type)
        result = extract_content(
            html,
            source.url,
            search_summary=source.search_summary or "",
            snippet=source.snippet,
        )
        self._store(source, result.text, result.extractor, result.origin)

    def _apply_fallback(self, source: Source) -> None:
        """抓取没成功时，退到搜索摘要 / snippet。"""
        result = extract_content(
            "",
            source.url,
            search_summary=source.search_summary or "",
            snippet=source.snippet,
        )
        if result.text:
            # 保留原始失败状态，但正文来路如实标成降级来源
            self._store(source, result.text, result.extractor, result.origin)
        else:
            self._store(source, "", None, ContentOrigin.snippet)

    def _store(
        self, source: Source, text: str, extractor: str | None, origin: ContentOrigin
    ) -> None:
        limit = self._settings.max_content_chars
        source.content = text[:limit] if text else None
        source.content_chars = len(source.content or "")
        source.content_hash = content_hash(source.content) if source.content else None
        source.extractor = extractor
        source.content_origin = origin if source.content else ContentOrigin.snippet


class _UnsupportedTypeError(httpx.HTTPError):
    """响应不是 HTML —— 用异常走统一的失败处理路径。"""


def make_source(
    *,
    sid: str,
    url: str,
    title: str = "",
    site_name: str = "",
    snippet: str = "",
    search_summary: str | None = None,
    published_at=None,
    round_no: int = 0,
    query: str = "",
) -> Source:
    """从一条搜索结果构造 Source（去重通过、分配 sid 之后调用）。"""
    return Source(
        sid=sid,
        url=url,
        normalized_url=normalize_url(url),
        title=title,
        site_name=site_name,
        domain=registrable_domain(url),
        snippet=snippet,
        search_summary=search_summary,
        published_at=published_at,
        first_seen_round=round_no,
        found_by_queries=[query] if query else [],
    )


__all__ = ["Fetcher", "make_source"]
