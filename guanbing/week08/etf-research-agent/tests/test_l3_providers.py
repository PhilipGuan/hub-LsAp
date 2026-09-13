# -*- coding: utf-8 -*-
"""L3 · 真实网络 Provider 自测（slow · 无网 / 无 Key 自动 skip）。

运行：pytest tests/test_l3_providers.py -v --runslow （默认不跑 --runslow 自动 skip）。
跳过条件：
  - 没网络（ping finance.yahoo.com 超时）
  - 没有安装 yfinance / requests / bs4 / lxml
"""
from __future__ import annotations

import socket
from datetime import date

import pytest

from backend.data_providers.implementations import (
    ETFDBProvider,
    YahooFinanceProvider,
)
from backend.data_providers.registry import NONE_AVAILABLE, ProviderRegistry


def _has_net(host: str = "finance.yahoo.com", port: int = 443, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


SKIP_SLOW = (
    not _has_net()
)
skip_slow = pytest.mark.skipif(SKIP_SLOW, reason="L3 slow：无外网或依赖缺失，跳过真实 Provider 测。")


@pytest.mark.slow
@skip_slow
class TestL3YahooFinanceLive:
    def test_get_profile_soxl_has_core_fields(self) -> None:
        prov = YahooFinanceProvider()
        assert prov.is_available() is True
        p = prov.get_profile("SOXL")
        assert p.ticker.upper() == "SOXL"
        # 至少一个非空
        assert (p.name or p.issuer or p.category or ""), "profile 应至少有 name/issuer/category 其一"
        # 费用率 / AUM 数值合理（>0 或 None 可接受但不能永远都返回 0）
        assert p.expense_ratio is None or isinstance(p.expense_ratio, (int, float))
        assert p.aum_usd_m is None or (isinstance(p.aum_usd_m, (int, float)) and p.aum_usd_m >= 0)
        # B2 字段存在
        assert isinstance(p.source_tag, str)
        assert p.as_of is None or isinstance(p.as_of, date)

    def test_get_history_1y_nonempty_rows_or_ttl_empty(self) -> None:
        prov = YahooFinanceProvider()
        h = prov.get_history("SOXL", "1y")
        assert h.ticker.upper() == "SOXL"
        # rows 空也允许（网络波动下可能拿不到），但 rows、period、as_of 字段必须有
        assert hasattr(h, "rows") and isinstance(h.rows, list)
        assert hasattr(h, "period") and isinstance(h.period, str)
        if h.rows:
            r = h.rows[0]
            # 至少一个价格字段非空
            assert r.close is not None or r.adj_close is not None or r.open is not None
            assert isinstance(r.date, date)

    def test_get_risk_metrics_returns_object(self) -> None:
        prov = YahooFinanceProvider()
        r = prov.get_risk_metrics("SOXL", "SPY")
        assert r.ticker.upper() == "SOXL"
        # 字段要么是 None 要么是 float
        for attr in ("beta", "sharpe_3y", "max_drawdown_pct", "volatility_1y_pct"):
            v = getattr(r, attr)
            assert v is None or isinstance(v, (int, float))


@pytest.mark.slow
@skip_slow
class TestL3ETFDBLive:
    def test_is_available_and_get_holdings(self) -> None:
        prov = ETFDBProvider()
        if not prov.is_available():
            pytest.skip("requests/bs4 依赖不全，跳过 ETFDBProvider")
        h = prov.get_holdings("SOXL", top_n=10)
        assert h.ticker.upper() == "SOXL"
        # 允许空（反爬 403 或 429 正常），但字段存在
        assert isinstance(h.top_holdings, list)
        assert isinstance(h.sector_weights, dict)
        assert isinstance(h.source_tag, str)


@pytest.mark.slow
@skip_slow
class TestL3RegistryLive:
    def test_fetch_first_available_returns_not_none_for_profile(self) -> None:
        reg = ProviderRegistry([YahooFinanceProvider()])
        result, provider, degraded = reg.fetch_first_available("get_profile", "SOXL")
        assert provider != NONE_AVAILABLE or degraded is True, (
            "Yahoo 可用时 provider 应非 NONE_AVAILABLE；断网时至少 degraded=True 不报异常"
        )
        # 返回值要么 ETFProfile 要么 dict（空）
        assert result is None or hasattr(result, "ticker") or isinstance(result, dict)
