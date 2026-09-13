# -*- coding: utf-8 -*-
"""M3 · CitationParser + FreshnessValidator（Spec §6 M3 正式实现）。"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from ..models import Citation, StaleField

_CITATION_RE = re.compile(r"\[([A-Z][A-Z0-9_]{1,15})-(\d{1,5})\]")

_PROVIDER_URL_FALLBACK: dict[str, str] = {
    "YAH": "https://finance.yahoo.com",
    "ETFDB": "https://etfdb.com",
    "POL": "https://polygon.io",
    "AV": "https://www.alphavantage.co",
    "BOCHA": "https://bocha.ai",
}


class CitationParser:
    """正则 `[PROV-N]` 行内引用扫描。"""

    def scan_citations(self, markdown: str) -> list[Citation]:
        if not markdown:
            return []
        seen: set[tuple[str, int]] = set()
        out: list[Citation] = []
        for m in _CITATION_RE.finditer(markdown):
            provider = m.group(1).upper()
            try:
                ref_index = int(m.group(2))
            except ValueError:
                continue
            if ref_index <= 0 or (provider, ref_index) in seen:
                continue
            seen.add((provider, ref_index))
            out.append(
                Citation(
                    tag=f"[{provider}-{ref_index}]",
                    provider=provider,
                    ref_index=ref_index,
                    source_url=_PROVIDER_URL_FALLBACK.get(provider),
                    snippet=None,
                    as_of=None,
                )
            )
        out.sort(key=lambda c: (c.provider, c.ref_index))
        return out

    def render_footnote(self, citations: list[Citation]) -> str:
        if not citations:
            return ""
        lines = ["---", ""]
        by_provider: dict[str, list[Citation]] = {}
        for c in citations:
            by_provider.setdefault(c.provider, []).append(c)
        for provider in sorted(by_provider):
            items = sorted(by_provider[provider], key=lambda x: x.ref_index)
            lines.append(f"**{provider}**")
            for c in items:
                url = c.source_url or _PROVIDER_URL_FALLBACK.get(provider, "")
                date_suffix = f" · as_of {c.as_of.isoformat()}" if c.as_of else ""
                snippet = f" — {c.snippet}" if c.snippet else ""
                lines.append(f"- [{provider}-{c.ref_index}] {url}{date_suffix}{snippet}")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"


def _is_date(v: Any) -> bool:
    return isinstance(v, (date, datetime))


def _as_date(v: Any) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return None


class FreshnessValidator:
    """字段时效性（Spec §6 M3 YTD=7d / fundflow5d=3d / aum=30d 等）。"""

    POLICIES: dict[str, int] = {
        "profile.ytd_return_pct":     7,
        "profile.one_y_return_pct":   7,
        "performance.adj_close":      3,
        "fundflow.flow_5d_usd_m":     3,
        "fundflow.flow_20d_usd_m":    5,
        "holdings.top_holdings":      30,
        "profile.aum_usd_m":          30,
        "profile.expense_ratio":      90,
        "risk_metrics.sharpe_3y":     30,
    }

    def _policy_for(self, path: str, field: str) -> int | None:
        full = f"{path}.{field}" if path else field
        direct = self.POLICIES.get(full)
        if direct is not None:
            return direct
        # 模糊匹配：路径末尾段等于策略前缀末尾段，或 key 本身就等于 field
        for key, days in self.POLICIES.items():
            if "." not in key and key == field:
                return days
            key_tail = key.split(".")[-1] if "." in key else key
            if field == key_tail and (path == "" or key.startswith(path + ".") or key.rsplit(".", 1)[0] == path):
                return days
        return None

    def _walk(self, obj: Any, path: str, today: date, out: list[StaleField]) -> None:
        if obj is None:
            return
        if hasattr(obj, "model_dump"):
            obj = obj.model_dump(by_alias=True)
        if isinstance(obj, dict):
            as_of_d = None
            if "as_of" in obj:
                as_of_d = _as_date(obj["as_of"])
            # 对每个非 as_of 字段检查策略；如果有 as_of 则用该日期计算每个字段的 staleness
            keys = [k for k in obj.keys() if k != "as_of"]
            if as_of_d is not None and keys:
                age = (today - as_of_d).days
                for k in keys:
                    max_days = self._policy_for(path, k)
                    if max_days is None:
                        continue
                    if age > max_days:
                        field_path = f"{path}.{k}" if path else k
                        out.append(
                            StaleField(
                                field_path=field_path,
                                as_of=as_of_d,
                                age_days=age,
                                policy_max_days=max_days,
                                suggested_action="bypass_cache_refetch" if age > max_days * 3 else "degrade_mark_warning",
                            )
                        )
            # 没有策略但有 as_of，仍尝试全局策略（如 aum_usd_m / expense_ratio 直接 match）
            elif as_of_d is not None:
                age = (today - as_of_d).days
                for k in keys:
                    max_days = self.POLICIES.get(k)
                    if max_days is not None and age > max_days:
                        field_path = f"{path}.{k}" if path else k
                        out.append(
                            StaleField(
                                field_path=field_path,
                                as_of=as_of_d,
                                age_days=age,
                                policy_max_days=max_days,
                                suggested_action="bypass_cache_refetch" if age > max_days * 3 else "degrade_mark_warning",
                            )
                        )
            for k in keys:
                sub = f"{path}.{k}" if path else str(k)
                self._walk(obj[k], sub, today, out)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                sub = f"{path}[{i}]" if path else f"[{i}]"
                self._walk(item, sub, today, out)

    def check(
        self,
        data: Any,
        path_prefix: str = "",
        today: date | None = None,
    ) -> list[StaleField]:
        today = today or date.today()
        out: list[StaleField] = []
        self._walk(data, path_prefix, today, out)
        return out
