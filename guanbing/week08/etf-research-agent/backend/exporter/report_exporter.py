# -*- coding: utf-8 -*-
"""M4 · ReportExporter 4 种格式（B2 CSV 先实现 as_of + source_tag 强制列头 · 其余占位）。
B1 合规：所有导出文件的 header / footer 都会内置 Disclaimer 注入。"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterable, Literal, Sequence

from ..models import (
    Disclaimer,
    ETFProfile,
    Holdings,
    PeerComparisonMatrix,
    PeerInfoRow,
    QuoteHistory,
    ReportContent,
)


def _ensure_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _write_csv(path: Path, header: Sequence[str], rows: Iterable[Sequence[object]]) -> Path:
    _ensure_dir(path)
    hdr = list(header)
    for required in ("as_of", "source_tag"):
        if required not in hdr:
            hdr.append(required)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(hdr)
        for r in rows:
            w.writerow(list(r))
    return path


class ReportExporter:
    """4 种格式（JSON / Markdown / HTML / CSV×4）+ B1 三位置免责声明。"""

    def __init__(self) -> None:
        self._stash_profile: ETFProfile | None = None
        self._stash_history: QuoteHistory | None = None
        self._stash_holdings: Holdings | None = None

    # ---- B1 Disclaimer 注入（Dry-Run 文本渲染） ----
    def inject_disclaimer(self, disclaimer: Disclaimer, where: Literal["top", "before_conclusion", "footer"]) -> str:
        title = {"top": "【顶部免责声明】", "before_conclusion": "【结论前免责声明】", "footer": "【页脚免责声明】"}.get(where, "【免责声明】")
        lines = [f"### {title}", ""]
        for s in disclaimer.statements:
            lines.append(f"- {s}")
        lines.append("")
        return "\n".join(lines)

    # ---- 结构化导出 ----
    def export_json(self, report: ReportContent, path: Path) -> Path:
        _ensure_dir(path)
        data = report.model_dump(mode="json")
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def export_markdown(self, report: ReportContent, path: Path) -> Path:
        _ensure_dir(path)
        top = self.inject_disclaimer(report.disclaimer, "top")
        before = self.inject_disclaimer(report.disclaimer, "before_conclusion")
        footer = self.inject_disclaimer(report.disclaimer, "footer")
        parts = [
            f"# ETF Research Report · {report.ticker} · {report.report_date.isoformat()}",
            f"Style: `{report.style}`  ·  Confidence: **{report.confidence.overall}**",
            "",
            top,
            "## 正文",
            "",
        ]
        for sec in report.sections:
            parts.append(f"### {sec.section_id}\n")
            parts.append(sec.plain_text + "\n")
        parts.append(before)
        if report.analyst_rating:
            parts.append(f"## 主题标签：{report.analyst_rating.rating.upper()}\n")
        parts.append(footer)
        path.write_text("\n".join(parts), encoding="utf-8")
        return path

    def export_html(self, report: ReportContent, path: Path) -> Path:
        md_path = path.with_suffix(path.suffix + ".md")
        self.export_markdown(report, md_path)
        body = md_path.read_text(encoding="utf-8").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        html = f"<!doctype html><html><head><meta charset=\"utf-8\"><title>{report.ticker} Report</title></head><body><pre>{body}</pre></body></html>"
        path.write_text(html, encoding="utf-8")
        try:
            md_path.unlink()
        except OSError:
            pass
        return path

    # ---- B2 强制 CSV：所有输出必须包含 as_of + source_tag ----
    def export_profile_csv(self, report: ReportContent, path: Path) -> Path:
        p = self._stash_profile or ETFProfile(
            ticker=report.ticker,
            name="",
            issuer="",
            isin="",
            inception_date=None,
            category="",
            focus_index="",
            expense_ratio=None,
            aum_usd_m=None,
            market_cap_usd_m=None,
            pe_ratio=None,
            pb_ratio=None,
            dividend_yield_pct=None,
            as_of=getattr(report, "report_date", None),
            source_tag="STUB",
        )
        header = [
            "ticker", "name", "issuer", "isin", "inception_date", "category", "focus_index",
            "expense_ratio", "aum_usd_m", "market_cap_usd_m", "pe_ratio", "pb_ratio",
            "dividend_yield_pct", "as_of", "source_tag",
        ]
        row = [
            p.ticker, p.name, p.issuer, p.isin,
            p.inception_date.isoformat() if p.inception_date else "",
            p.category, p.focus_index,
            p.expense_ratio if p.expense_ratio is not None else "",
            p.aum_usd_m if p.aum_usd_m is not None else "",
            p.market_cap_usd_m if p.market_cap_usd_m is not None else "",
            p.pe_ratio if p.pe_ratio is not None else "",
            p.pb_ratio if p.pb_ratio is not None else "",
            p.dividend_yield_pct if p.dividend_yield_pct is not None else "",
            p.as_of.isoformat() if p.as_of else "",
            p.source_tag or "",
        ]
        return _write_csv(path, header, [row])

    def export_history_csv(self, report: ReportContent, path: Path) -> Path:
        h = self._stash_history or QuoteHistory(
            ticker=report.ticker, period="", rows=[],
            as_of=getattr(report, "report_date", None), source_tag="STUB",
        )
        header = ["date", "open", "high", "low", "close", "adj_close", "volume", "as_of", "source_tag"]
        rows: list[list[object]] = []
        as_of_s = h.as_of.isoformat() if h.as_of else ""
        for r in h.rows:
            rows.append([
                r.date.isoformat() if r.date else "",
                r.open if r.open is not None else "",
                r.high if r.high is not None else "",
                r.low if r.low is not None else "",
                r.close if r.close is not None else "",
                r.adj_close if r.adj_close is not None else "",
                r.volume if r.volume is not None else "",
                as_of_s,
                h.source_tag or "",
            ])
        if not rows:
            rows.append([""] * 7 + [as_of_s, h.source_tag or ""])
        return _write_csv(path, header, rows)

    def export_holdings_csv(self, report: ReportContent, path: Path) -> Path:
        hd = self._stash_holdings or Holdings(
            ticker=report.ticker, top_holdings=[], sector_weights={},
            as_of=getattr(report, "report_date", None), source_tag="STUB",
        )
        header = ["ticker", "holding_name", "holding_ticker", "weight_pct", "sector", "as_of", "source_tag"]
        rows: list[list[object]] = []
        as_of_s = hd.as_of.isoformat() if hd.as_of else ""
        for item in hd.top_holdings:
            rows.append([
                hd.ticker, item.name or "", item.ticker or "",
                item.weight_pct if item.weight_pct is not None else "",
                item.sector or "", as_of_s, hd.source_tag or "",
            ])
        if not rows:
            rows.append([hd.ticker, "", "", "", "", as_of_s, hd.source_tag or ""])
        return _write_csv(path, header, rows)

    def export_peer_matrix_csv(
        self, matrix: PeerComparisonMatrix, path: Path
    ) -> Path:
        header = [
            "ticker", "name", "aum_usd_m", "expense_ratio",
            "ytd_return_pct", "one_y_return_pct", "three_y_annual_pct",
            "sharpe_3y", "max_drawdown_pct", "beta", "dividend_yield_pct",
            "as_of", "source_tag",
        ]
        rows: list[list[object]] = []
        latest_s = matrix.as_of.isoformat() if matrix.as_of else ""
        src_by_row: dict[str, str] = {}
        for r in matrix.rows:
            src_by_row[r.ticker] = r.source_tag or ""
        for r in matrix.rows or [PeerInfoRow(ticker=matrix.main_ticker or "")]:
            as_of_s = (r.as_of.isoformat() if r.as_of else "") or latest_s
            rows.append([
                r.ticker, r.name or "",
                r.aum_usd_m if r.aum_usd_m is not None else "",
                r.expense_ratio if r.expense_ratio is not None else "",
                r.ytd_return_pct if r.ytd_return_pct is not None else "",
                r.one_y_return_pct if r.one_y_return_pct is not None else "",
                r.three_y_annual_pct if r.three_y_annual_pct is not None else "",
                r.sharpe_3y if r.sharpe_3y is not None else "",
                r.max_drawdown_pct if r.max_drawdown_pct is not None else "",
                r.beta if r.beta is not None else "",
                r.dividend_yield_pct if r.dividend_yield_pct is not None else "",
                as_of_s,
                r.source_tag or src_by_row.get(r.ticker, "STUB"),
            ])
        return _write_csv(path, header, rows)
