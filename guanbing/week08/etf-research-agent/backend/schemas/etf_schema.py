# -*- coding: utf-8 -*-
"""M2 · ETFSchema + PeerResolver（Spec §6 M2 正式实现）。
本节节顺序固定不可乱，用于 JudgeAgent/Judge 收敛检查。"""
from __future__ import annotations

from typing import Literal

from ..models import PeerInfoRow, PeerProfileMini, PeerResolution, PeerComparisonMatrix


DEFAULT_REPORT_SECTIONS: list[tuple[str, str]] = [
    ("fundamentals",       "1. 基金概况与管理人信息"),
    ("performance_1y",     "2. 近 1 年 / YTD 价格表现 & 回撤分析"),
    ("holdings",           "3. 持仓 Top15 · 行业 & 风格暴露"),
    ("risk_metrics",       "4. 风险指标（夏普 / 最大回撤 / Beta）"),
    ("fund_flows",         "5. 资金流向（5d/20d/60d/YTD）"),
    ("peer_comparison",    "6. 同类 5~8 只 ETF 矩阵对比"),
    ("competitive_edges",  "7. 相对同类的差异化优势 / 劣势"),
    ("catalysts",          "8. 未来 3~6 个月正面 / 负面催化因素"),
    ("risks",              "9. 尾部风险 & 最坏情景描述"),
    ("thesis_conclusion",  "10. 综合结论（多空理由 × 主题标签）"),
]


_CATEGORY_PEER_BUCKETS: dict[str, list[tuple[str, str, str, str]]] = {
    "semiconductor": [
        ("SOXX", "iShares Semiconductor ETF", "Semiconductor", "ICE Semiconductor Index"),
        ("SMH", "VanEck Semiconductor ETF", "Semiconductor", "MVIS Global Listed Semi"),
        ("SOXL", "Direxion Daily Semi Bull 3x", "Leveraged Semiconductor", "ICE Semiconductor 3x"),
        ("SOXS", "Direxion Daily Semi Bear 3x", "Inverse Semiconductor", "ICE Semiconductor -3x"),
        ("PSI", "Invesco Dynamic Semiconductors", "Semiconductor", "Dynamic Semiconductor"),
        ("FTXL", "First Trust Nasdaq Semiconductor", "Semiconductor", "Nasdaq Semiconductor"),
        ("FLAT", "", "", ""),
    ],
    "nasdaq_100": [
        ("QQQ", "Invesco QQQ Trust", "Large Cap Growth", "Nasdaq-100"),
        ("QQQM", "Invesco Nasdaq 100 ETF", "Large Cap Growth", "Nasdaq-100"),
        ("ONEQ", "Fidelity Nasdaq Composite", "Large Cap Blend", "Nasdaq Composite"),
        ("XLK", "Technology Select Sector SPDR", "Technology Sector", "S&P 500 Tech"),
        ("VGT", "Vanguard Information Tech", "Technology Sector", "MSCI US Investable Mkts IT"),
        ("IYW", "iShares U.S. Technology", "Technology Sector", "Dow Jones US Tech"),
        ("QTEC", "First Trust Nasdaq-100 Tech", "Technology Sector", "Nasdaq-100 Ex-Financials"),
        ("TQQQ", "ProShares UltraPro QQQ 3x", "Leveraged Nasdaq", "Nasdaq-100 3x"),
    ],
    "s_and_p_500": [
        ("SPY", "SPDR S&P 500 ETF", "Large Cap Blend", "S&P 500"),
        ("IVV", "iShares Core S&P 500", "Large Cap Blend", "S&P 500"),
        ("VOO", "Vanguard S&P 500 ETF", "Large Cap Blend", "S&P 500"),
        ("SPLG", "SPDR Portfolio S&P 500", "Large Cap Blend", "S&P 500"),
        ("UPRO", "ProShares Ultra S&P500 3x", "Leveraged Market", "S&P 500 3x"),
        ("SH", "ProShares Short S&P 500", "Inverse Market", "S&P 500 -1x"),
        ("SPUU", "Direxion Daily S&P 500 Bull 2x", "Leveraged Market", "S&P 500 2x"),
        ("RSP", "Invesco S&P 500 Equal Weight", "Equal Weight", "S&P 500 Equal Weight"),
    ],
    "artificial_intelligence": [
        ("BOTZ", "Global X Robotics & AI", "AI/Robotics", "Indxx Global Robotics & AI"),
        ("AIQ", "Global X Artificial Intelligence", "AI/Robotics", "AI & Big Data"),
        ("ROBT", "First Trust Nasdaq AI & Robotics", "AI/Robotics", "Nasdaq CTA AI & Robotics"),
        ("UBOT", "Direxion Daily Robotics Bull 2x", "Leveraged AI", "Indxx Robotics & AI 2x"),
        ("FLAT", "", "", ""),
    ],
    "healthcare": [
        ("XLV", "Health Care Select Sector SPDR", "Healthcare Sector", "S&P 500 Health Care"),
        ("VHT", "Vanguard Health Care", "Healthcare Sector", "MSCI US Investable Mkts Health Care"),
        ("IYH", "iShares U.S. Healthcare", "Healthcare Sector", "Dow Jones US Health Care"),
        ("XBI", "SPDR S&P Biotech ETF", "Biotech", "S&P Biotech Select Industry"),
        ("IBB", "iShares Biotechnology", "Biotech", "NASDAQ Biotechnology"),
        ("BBH", "VanEck Biotech ETF", "Biotech", "MVIS US Listed Biotech 25"),
        ("PJP", "Invesco Dynamic Pharmaceuticals", "Pharma", "Dynamic Pharmaceuticals"),
        ("XHE", "SPDR S&P Health Care Equipment", "Medical Devices", "S&P Health Care Equipment"),
    ],
    "energy": [
        ("XLE", "Energy Select Sector SPDR", "Energy Sector", "S&P 500 Energy"),
        ("VDE", "Vanguard Energy", "Energy Sector", "MSCI US Investable Mkts Energy"),
        ("IYE", "iShares U.S. Energy", "Energy Sector", "Dow Jones US Energy"),
        ("OIH", "VanEck Oil Services ETF", "Oil Services", "MVIS US Listed Oil Services 25"),
        ("XOP", "SPDR S&P Oil & Gas Exploration", "E&P", "S&P Oil & Gas E&P Select Industry"),
        ("AMLP", "ALPS Alerian MLP ETF", "MLP", "Alerian MLP Infrastructure"),
        ("UNG", "United States Natural Gas Fund", "Commodity Gas", "Natural Gas Futures"),
        ("USO", "United States Oil Fund", "Commodity Oil", "WTI Crude Futures"),
    ],
    "fixed_income": [
        ("AGG", "iShares Core US Aggregate Bond", "Aggregate Bond", "Bloomberg US Aggregate"),
        ("BND", "Vanguard Total Bond Market", "Aggregate Bond", "Bloomberg US Aggregate Float Adj"),
        ("TLT", "iShares 20+ Year Treasury", "Long Treasury", "ICE US Treasury 20+ Yr"),
        ("IEF", "iShares 7-10 Year Treasury", "Intermediate Treasury", "ICE US Treasury 7-10 Yr"),
        ("SHY", "iShares 1-3 Year Treasury", "Short Treasury", "ICE US Treasury 1-3 Yr"),
        ("TIP", "iShares TIPS Bond ETF", "TIPS", "Bloomberg US TIPS"),
        ("LQD", "iShares iBoxx $ Inv Grade Corp", "Investment Grade Corp", "Markit iBoxx USD Liquid IG"),
        ("HYG", "iShares iBoxx $ High Yield Corp", "High Yield Corp", "Markit iBoxx USD Liquid HY"),
    ],
    "gold_commodity": [
        ("GLD", "SPDR Gold Shares", "Physical Gold", "Gold Spot London PM Fix"),
        ("IAU", "iShares Gold Trust", "Physical Gold", "LBMA Gold Price PM"),
        ("SLV", "iShares Silver Trust", "Physical Silver", "LBMA Silver Price"),
        ("GDX", "VanEck Gold Miners ETF", "Gold Equities", "NYSE Arca Gold Miners"),
        ("GDXJ", "VanEck Junior Gold Miners", "Junior Gold Equities", "MVIS Global Junior Gold Miners"),
        ("NUGT", "Direxion Daily Gold Miners Bull 2x", "Leveraged Gold", "NYSE Arca Gold Miners 2x"),
        ("DUST", "Direxion Daily Gold Miners Bear 2x", "Inverse Gold", "NYSE Arca Gold Miners -2x"),
        ("USCI", "United States Commodity Index", "Broad Commodity", "SummerHaven Dynamic Commodity"),
    ],
}


def _categorize(category: str, focus_index: str) -> str:
    blob = f"{category} {focus_index}".lower()
    if any(k in blob for k in ("semi", "semiconductor", "chip", "soxx", "smh", "soxl")):
        return "semiconductor"
    if any(k in blob for k in ("nasdaq", "qqq", "oneq")):
        return "nasdaq_100"
    if any(k in blob for k in ("s&p 500", "sp500", "spy ", "ivv", "voo")):
        return "s_and_p_500"
    if any(k in blob for k in ("ai ", "artificial", "robot", "botz")):
        return "artificial_intelligence"
    if any(k in blob for k in ("health", "biotech", "pharma", "xlv", "ibb", "xbi")):
        return "healthcare"
    if any(k in blob for k in ("energy", "oil", "gas", "xle", "oih", "xop")):
        return "energy"
    if any(k in blob for k in ("bond", "treasury", "fixed", "agg", "bnd", "tlt", "lqd")):
        return "fixed_income"
    if any(k in blob for k in ("gold", "silver", "commodity", "gld", "iau", "slv", "gdx")):
        return "gold_commodity"
    return "nasdaq_100"


class ETFSchema:
    """固定报告结构（10 节）+ 检查哪些节缺内容。"""

    section_ids: list[str] = [sid for sid, _ in DEFAULT_REPORT_SECTIONS]
    section_titles: list[str] = [t for _, t in DEFAULT_REPORT_SECTIONS]

    def section_id_to_title(self, section_id: str) -> str:
        return dict(DEFAULT_REPORT_SECTIONS).get(section_id, section_id)

    def identify_missing_sections(self, filled_section_ids: set[str]) -> list[str]:
        filled = {sid for sid in filled_section_ids if sid}
        return [sid for sid in self.section_ids if sid not in filled]


class PeerResolver:
    """同类 ETF 发现（Spec §6 M2 PeerResolver 4 步 + 常见行业 8 组兜底）。"""

    def resolve_peers(
        self,
        category: str,
        focus_index: str,
        aum_usd_b: float,
        count: Literal[5, 6, 7, 8] = 5,
        extra_peer_tickers: list[str] | None = None,
    ) -> PeerResolution:
        key = _categorize(category, focus_index)
        bucket = _CATEGORY_PEER_BUCKETS.get(key, _CATEGORY_PEER_BUCKETS["nasdaq_100"])
        profiles: list[PeerProfileMini] = []
        seen: set[str] = set()
        for t, n, c, f in bucket:
            if not t or not n or t in seen:
                continue
            seen.add(t)
            profiles.append(
                PeerProfileMini(
                    ticker=t,
                    name=n or t,
                    category=c or key,
                    focus_index=f or focus_index or key,
                    aum_usd_m=None,
                )
            )
        default_n = len(profiles)
        if extra_peer_tickers:
            for t in extra_peer_tickers:
                t = t.strip().upper()
                if not t or t in seen:
                    continue
                seen.add(t)
                profiles.append(
                    PeerProfileMini(
                        ticker=t,
                        name=t,
                        category=category or key,
                        focus_index=focus_index or key,
                        aum_usd_m=None,
                    )
                )
        if not profiles:
            return PeerResolution(peer_tickers=[], peer_basis="empty", peer_profiles=[])
        n_extra_added = len(profiles) - default_n
        base_cap = max(5, min(8, int(count)))
        cap = base_cap
        if n_extra_added > 0:
            cap = max(base_cap, default_n + n_extra_added)
        profiles = profiles[:cap]
        return PeerResolution(
            peer_tickers=[p.ticker for p in profiles],
            peer_basis=f"category={category or key}; focus_index={focus_index or key}; aum_usd_b≈{aum_usd_b:.2f}; bucket={key}",
            peer_profiles=profiles,
        )

    def assemble_matrix(self, peer_profiles: list[PeerInfoRow]) -> PeerComparisonMatrix:
        if not peer_profiles:
            return PeerComparisonMatrix(main_ticker="", rows=[], as_of=None)
        rows = sorted(
            peer_profiles,
            key=lambda r: (-(r.aum_usd_m or 0.0), r.ticker),
        )
        latest = max((r.as_of for r in rows if r.as_of), default=None)
        return PeerComparisonMatrix(
            main_ticker=rows[0].ticker,
            rows=rows,
            as_of=latest,
        )
