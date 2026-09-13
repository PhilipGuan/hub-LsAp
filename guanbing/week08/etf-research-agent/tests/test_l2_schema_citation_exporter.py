# -*- coding: utf-8 -*-
"""L2 · Schema + Citation + Freshness + Exporter 列头（无网 · Spec §8 L2）。"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from backend.citations.parser import CitationParser, FreshnessValidator
from backend.exporter.report_exporter import ReportExporter
from backend.models import (
    ETFProfile,
    FundFlow,
    PeerComparisonMatrix,
    PeerInfoRow,
    RiskMetrics,
)
from backend.schemas.etf_schema import ETFSchema, PeerResolver


class TestL2ETFSchema:
    def test_default_10_sections(self) -> None:
        s = ETFSchema()
        assert len(s.section_ids) == 10
        assert len(s.section_titles) == 10
        assert s.section_ids[0] == "fundamentals"
        assert s.section_ids[-1] == "thesis_conclusion"

    def test_identify_missing_sections_returns_correct_count(self) -> None:
        s = ETFSchema()
        filled = {"fundamentals", "thesis_conclusion", "performance_1y"}
        missing = s.identify_missing_sections(filled)
        assert len(missing) == 10 - 3
        assert "holdings" in missing
        assert "fundamentals" not in missing
        assert "thesis_conclusion" not in missing

    def test_section_id_to_title(self) -> None:
        s = ETFSchema()
        assert s.section_id_to_title("fundamentals").startswith("1.")
        assert s.section_id_to_title("__nonexistent__") == "__nonexistent__"


class TestL2PeerResolver:
    def test_resolve_peers_semiconductor_counts_5_to_8(self) -> None:
        r = PeerResolver()
        res = r.resolve_peers("Semiconductor", "ICE Semi Index", 10.0, count=6)
        assert 5 <= len(res.peer_tickers) <= 8
        assert "SOXX" in res.peer_tickers or "SMH" in res.peer_tickers
        assert "semi" in res.peer_basis.lower() or "Semiconductor" in res.peer_basis

    def test_assemble_matrix_sorts_by_aum_desc(self) -> None:
        today = date.today()
        rows = [
            PeerInfoRow(ticker="B", aum_usd_m=200.0, as_of=today),
            PeerInfoRow(ticker="A", aum_usd_m=500.0, as_of=today - timedelta(days=1)),
            PeerInfoRow(ticker="C", aum_usd_m=None, as_of=today),
        ]
        mat = PeerResolver().assemble_matrix(rows)
        assert isinstance(mat, PeerComparisonMatrix)
        assert mat.main_ticker == "A"
        assert [r.ticker for r in mat.rows[:2]] == ["A", "B"]
        assert mat.as_of == today

    def test_resolve_peers_extra_tickers_dedup(self) -> None:
        r = PeerResolver()
        res = r.resolve_peers("S&P 500", "S&P 500", 500.0, count=5, extra_peer_tickers=["spy", "SPY", "XLE"])
        assert res.peer_tickers.count("SPY") == 1
        assert "XLE" in res.peer_tickers


class TestL2CitationParser:
    def test_scan_citations_hit_2_and_miss_1(self) -> None:
        text = (
            "YTD 上涨 12% [YAH-1]，费用率 0.40% [ETFDB-7]。"
            "无引用的一行。"
            "坏格式 [yah-0] [BAD_XX] [A-999999] 不应该被解析。"
        )
        cits = CitationParser().scan_citations(text)
        tags = {c.tag for c in cits}
        assert "[YAH-1]" in tags
        assert "[ETFDB-7]" in tags
        # miss：yah 小写不符合大写首字母的严格规则（本实现要大写PROV），BAD_XX有下划线但索引是空/坏，A-999999数字超5位
        bad_tags = {c.tag for c in cits if c.provider in ("YAH0", "BAD_XX") or c.ref_index > 99999}
        assert len(bad_tags) == 0

    def test_render_footnote_contains_separator_and_provider_header(self) -> None:
        from backend.models import Citation

        cits = [
            Citation(tag="[YAH-1]", provider="YAH", ref_index=1, source_url="https://x"),
            Citation(tag="[ETFDB-2]", provider="ETFDB", ref_index=2),
        ]
        note = CitationParser().render_footnote(cits)
        assert note.startswith("---")
        assert "**YAH**" in note
        assert "**ETFDB**" in note
        assert "[YAH-1]" in note
        assert "[ETFDB-2]" in note


class TestL2FreshnessValidator:
    def test_as_of_stale_returns_1_stale_field(self) -> None:
        today = date.today()
        stale_date = today - timedelta(days=10)
        profile = ETFProfile(
            ticker="SOXL",
            aum_usd_m=1000.0,
            ytd_return_pct=12.3,  # type: ignore[call-arg]
            as_of=stale_date,
            source_tag="YAH",
        )
        fv = FreshnessValidator()
        stales = fv.check(profile, path_prefix="profile", today=today)
        # ytd_return_pct 策略 7d：10天>7d → 命中；aum_usd_m 30d：10天<30d → 不命中
        field_paths = {s.field_path for s in stales}
        assert any("ytd_return_pct" in p for p in field_paths), f"stales={stales}"
        assert len(stales) >= 1
        s0 = stales[0]
        assert s0.age_days == 10
        assert s0.policy_max_days <= 7
        assert s0.suggested_action in {"degrade_mark_warning", "bypass_cache_refetch"}

    def test_as_of_fresh_returns_empty(self) -> None:
        today = date.today()
        ff = FundFlow(ticker="SOXL", flow_5d_usd_m=10.0, as_of=today - timedelta(days=1), source_tag="YAH")
        stales = FreshnessValidator().check(ff, path_prefix="fundflow", today=today)
        assert stales == []


class TestL2ExporterCSVColumns:
    @pytest.fixture
    def _make_exporter(self):
        return ReportExporter()

    def test_4_csv_headers_require_as_of_and_source_tag(self, _make_exporter, tmp_path) -> None:
        exp: ReportExporter = _make_exporter
        today = date.today()
        from backend.models import (
            CitedParagraph,
            Confidence,
            Disclaimer,
            Holdings,
            ProcessLog,
            QuoteHistory,
            ReportContent,
        )

        # 先把需要的 provider 数据预先塞进 Exporter 内部属性（L2 只校验 CSV 列头，不校验 Exporter 真实现）
        profile = ETFProfile(ticker="SOXL", aum_usd_m=100.0, as_of=today, source_tag="YAH")
        history = QuoteHistory(ticker="SOXL", rows=[], as_of=today, source_tag="YAH")
        holdings = Holdings(ticker="SOXL", top_holdings=[], as_of=today, source_tag="YAH")
        peer_matrix = PeerComparisonMatrix(
            main_ticker="SOXL",
            rows=[PeerInfoRow(ticker="SOXL", aum_usd_m=100.0, as_of=today, source_tag="YAH")],
            as_of=today,
        )
        exp._stash_profile = profile  # type: ignore[attr-defined]
        exp._stash_history = history  # type: ignore[attr-defined]
        exp._stash_holdings = holdings  # type: ignore[attr-defined]

        report = ReportContent(
            ticker="SOXL",
            report_date=today,
            style="analyst",
            sections=[CitedParagraph(section_id="fundamentals", plain_text="x")],
            peer_matrix=peer_matrix,
            confidence=Confidence(overall="medium"),
            process=ProcessLog(),
            disclaimer=Disclaimer(),
        )

        import csv

        cases = [
            ("profile.csv", exp.export_profile_csv, report),
            ("history.csv", exp.export_history_csv, report),
            ("holdings.csv", exp.export_holdings_csv, report),
            ("peer.csv", exp.export_peer_matrix_csv, report.peer_matrix),
        ]
        for name, fn, arg in cases:
            p = tmp_path / name
            p2 = fn(arg, p)
            assert p2.exists() and p2.stat().st_size > 0
            with p2.open("r", encoding="utf-8", newline="") as f:
                reader = csv.reader(f)
                header = next(reader, [])
            assert "as_of" in header, f"{name} header missing as_of: {header}"
            assert "source_tag" in header, f"{name} header missing source_tag: {header}"
