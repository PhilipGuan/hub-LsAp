"""Bocha web-search client. Uses urllib stdlib — no httpx dependency."""
import json
import os
import urllib.error
import urllib.request

from .models import SearchResult


class BochaSearcher:
    # Docs: https://bocha-ai.feishu.cn/wiki/RXEOw02rFiwzGSkd9mUcqoeAnNK
    def __init__(self, api_key: str | None = None, base_url: str = "https://api.bocha.cn/v1/web-search"):
        self.api_key = api_key or os.environ["BOCHA_API_KEY"]
        self.base_url = base_url

    def search(self, query: str, count: int = 10, summary: bool = True) -> list[SearchResult]:
        body = json.dumps({"query": query, "summary": summary, "count": count}).encode("utf-8")
        req = urllib.request.Request(
            self.base_url, data=body, method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Bocha search failed: HTTP {e.code} {e.reason}") from e
        return self._parse(data)

    @staticmethod
    def _parse(data: dict) -> list[SearchResult]:
        # Bocha response shape (Bing-like): {"data": {"webPages": {"value": [...]}}}
        # Each item: {name, url, snippet, summary, siteName}
        # Ponytail: be lenient — fall back to alternate shapes if the API evolves.
        pages = (
            data.get("data", {}).get("webPages", {}).get("value")
            or data.get("data", {}).get("value")
            or data.get("value")
            or []
        )
        results: list[SearchResult] = []
        for p in pages:
            results.append(SearchResult(
                url=p.get("url", ""),
                title=p.get("name", "") or p.get("title", ""),
                snippet=p.get("snippet", ""),
                summary=p.get("summary", "") or "",
                source=p.get("siteName", "") or p.get("source", "") or "",
            ))
        return results