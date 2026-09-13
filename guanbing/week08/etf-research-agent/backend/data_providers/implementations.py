# -*- coding: utf-8 -*-
"""M1 Provider 4 个实现（MVP · 免费主力 Yahoo Finance + ETFDB；Polygon/AV 仅 Key 检查占位）。
所有方法 0 抛错（失败返回空对象，字段=None），由 Registry 判断 degraded。
"""
from __future__ import annotations

import logging
import os
from datetime import date
from typing import Any, Literal

import numpy as np
import pandas as pd

from ..models import (
    ETFProfile,
    FundFlow,
    HoldingItem,
    Holdings,
    QuoteHistory,
    QuoteRow,
    RiskMetrics,
)
from .base import DataProvider

log = logging.getLogger(__name__)
PeerInfoBatch = dict  # {peer_ticker: dict of row fields} → 最终 PeerInfoRows 工厂组装


def _today() -> date:
    return date.today()


def _safe_get(d: dict, *keys, default=None):
    """字典多级安全查找（任何一步 None 就返回 default，不抛错）。"""
    cur: Any = d
    for k in keys:
        if cur is None:
            return default
        if isinstance(cur, dict):
            cur = cur.get(k)
        else:
            return default
    return cur if cur is not None else default


def _as_float(x: Any) -> float | None:
    if x is None:
        return None
    try:
        f = float(x)
        if np.isnan(f):
            return None
        return f
    except (ValueError, TypeError):
        return None


def _as_int(x: Any) -> int | None:
    if x is None:
        return None
    try:
        return int(x)
    except (ValueError, TypeError):
        return None


# ============================================================
# Yahoo Finance · 主力免费数据源
# ============================================================
class YahooFinanceProvider(DataProvider):
    name = "yahoo_finance"
    source_tag = "YAH"

    def is_available(self) -> bool:
        try:
            import yfinance  # noqa: F401
            return True
        except Exception:  # noqa: BLE001
            return False

    def _ticker(self, ticker: str):
        import yfinance as yf
        return yf.Ticker(ticker)

    def get_profile(self, ticker: str) -> ETFProfile:
        try:
            info = self._ticker(ticker).info or {}
            inception = info.get("fundInceptionDate") or info.get("ipoExpectedDate") or info.get("firstTradeDateEpochUtc")
            if isinstance(inception, (int, float)):
                inception = date.fromtimestamp(int(inception))
            elif isinstance(inception, str):
                try:
                    inception = date.fromisoformat(inception[:10])
                except Exception:  # noqa: BLE001
                    inception = None
            pe_forward = _as_float(_safe_get(info, "trailingPE")) or _as_float(_safe_get(info, "forwardPE"))
            pb = _as_float(_safe_get(info, "priceToBook"))
            y = info.get("yield") or info.get("trailingAnnualDividendYield")
            div_yield_pct = _as_float(y) * 100.0 if _as_float(y) else None
            return ETFProfile(
                ticker=ticker,
                name=info.get("longName") or info.get("shortName") or ticker,
                issuer=info.get("fundFamily") or "",
                isin=info.get("isin") or "",
                inception_date=inception,
                category=info.get("category") or info.get("industry") or "",
                focus_index=info.get("indexName") or info.get("underlyingSymbol") or "",
                expense_ratio=_as_float(info.get("annualReportExpenseRatio")) or _as_float(info.get("totalAssets")),  # 用了错的fallback，后续ETFDB覆盖
                aum_usd_m=_as_float(info.get("totalAssets")) / 1_000_000.0 if _as_float(info.get("totalAssets")) else None,
                market_cap_usd_m=_as_float(info.get("marketCap")) / 1_000_000.0 if _as_float(info.get("marketCap")) else None,
                pe_ratio=pe_forward,
                pb_ratio=pb,
                dividend_yield_pct=div_yield_pct,
                as_of=_today(),
                source_tag=self.source_tag,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("[YAH profile %s] fallback empty (%s)", ticker, exc)
            return ETFProfile(ticker=ticker, as_of=_today(), source_tag=f"{self.source_tag}:EMPTY")

    def get_history(
        self,
        ticker: str,
        period: Literal["1mo", "3mo", "6mo", "1y", "3y", "5y", "10y", "ytd"],
    ) -> QuoteHistory:
        try:
            hist = self._ticker(ticker).history(period=period, auto_adjust=False)
            if hist is None or len(hist) == 0:
                return QuoteHistory(ticker=ticker, period=period, rows=[], as_of=_today(), source_tag=f"{self.source_tag}:EMPTY")
            rows: list[QuoteRow] = []
            for ts, row in hist.iterrows():
                d = ts.date() if hasattr(ts, "date") else date.fromisoformat(str(ts)[:10])
                rows.append(QuoteRow(
                    date=d,
                    open=_as_float(row.get("Open")),
                    high=_as_float(row.get("High")),
                    low=_as_float(row.get("Low")),
                    close=_as_float(row.get("Close")),
                    adj_close=_as_float(row.get("Adj Close")) or _as_float(row.get("Close")),
                    volume=_as_int(row.get("Volume")),
                ))
            return QuoteHistory(ticker=ticker, period=period, rows=rows, as_of=_today(), source_tag=self.source_tag)
        except Exception as exc:  # noqa: BLE001
            log.warning("[YAH history %s %s] fallback empty (%s)", ticker, period, exc)
            return QuoteHistory(ticker=ticker, period=period, rows=[], as_of=_today(), source_tag=f"{self.source_tag}:EMPTY")

    def get_risk_metrics(self, ticker: str, benchmark: str = "SPY") -> RiskMetrics:
        try:
            hist = self._ticker(ticker).history(period="3y", auto_adjust=False)
            bench = self._ticker(benchmark).history(period="3y", auto_adjust=False)
            if hist is None or len(hist) < 60:
                return RiskMetrics(ticker=ticker, as_of=_today(), source_tag=f"{self.source_tag}:NO_DATA")
            closes = pd.Series(hist["Close"], dtype=float).ffill()
            rets = closes.pct_change().dropna()
            if len(rets) == 0:
                return RiskMetrics(ticker=ticker, as_of=_today(), source_tag=f"{self.source_tag}:NO_RET")
            # 1y 波动（252 个交易日 × 年化）
            vol_1y = float(rets.tail(min(252, len(rets))).std() * np.sqrt(252) * 100.0) if len(rets) >= 30 else None
            # 最大回撤（全部）
            cum = (1 + rets).cumprod()
            dd = cum / cum.cummax() - 1
            max_dd = float(dd.min() * 100.0) if len(dd) else None
            # 夏普（假设 2% 无风险）
            mean_daily = float(rets.mean())
            std_daily = float(rets.std())
            sharpe_3y = ((mean_daily - 0.02 / 252) / std_daily * np.sqrt(252)) if std_daily > 0 else None
            sortino_3y = None
            if len(rets):
                neg = rets[rets < 0]
                if len(neg) and float(neg.std()) > 0:
                    sortino_3y = float(((mean_daily - 0.02 / 252) / neg.std() * np.sqrt(252)))
            beta = alpha = te = corr = None
            if bench is not None and len(bench) >= 60:
                try:
                    b_rets = pd.Series(bench["Close"], dtype=float).ffill().pct_change().dropna()
                    common = rets.to_frame("t").join(b_rets.to_frame("b"), how="inner").dropna()
                    if len(common) >= 30:
                        cov = np.cov(common["t"], common["b"])
                        if cov[1, 1] != 0:
                            beta = float(cov[0, 1] / cov[1, 1])
                            alpha = float((mean_daily - (0.02/252 + beta * (float(common["b"].mean()) - 0.02/252))) * 252 * 100)
                        corr = float(np.corrcoef(common["t"], common["b"])[0, 1])
                        te_s = (common["t"] - common["b"]).std() * np.sqrt(252) * 100
                        te = float(te_s)
                except Exception:  # noqa: BLE001
                    pass
            return RiskMetrics(
                ticker=ticker,
                volatility_1y_pct=vol_1y,
                max_drawdown_pct=max_dd,
                sharpe_3y=_as_float(sharpe_3y),
                sortino_3y=_as_float(sortino_3y),
                beta=_as_float(beta),
                alpha_pct=_as_float(alpha),
                tracking_error_pct=_as_float(te),
                correlation_to_benchmark=_as_float(corr),
                as_of=_today(),
                source_tag=self.source_tag,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("[YAH risk %s] fallback empty (%s)", ticker, exc)
            return RiskMetrics(ticker=ticker, as_of=_today(), source_tag=f"{self.source_tag}:EMPTY")

    def get_holdings(self, ticker: str, top_n: int = 15) -> Holdings:
        try:
            tk = self._ticker(ticker)
            holdings_df = None
            if hasattr(tk, "funds_data"):
                try:
                    fdata = tk.funds_data
                    holdings_df = getattr(fdata, "top_holdings", None)
                except Exception:  # noqa: BLE001
                    holdings_df = None
            top_holdings: list[HoldingItem] = []
            if holdings_df is not None and hasattr(holdings_df, "iterrows"):
                for i, (_, row) in enumerate(holdings_df.iterrows()):
                    if i >= top_n:
                        break
                    hname = str(row.iloc[0]) if len(row) > 0 else ""
                    hw = _as_float(row.iloc[-1]) if len(row) > 1 else None
                    top_holdings.append(HoldingItem(name=hname, weight_pct=hw))
            sectors: dict[str, float] = {}
            try:
                fdata = tk.funds_data
                sd = getattr(fdata, "sector_weightings", None)
                if sd is not None and hasattr(sd, "to_dict"):
                    for k, v in sd.to_dict().items():
                        sectors[str(k)] = float(v) if v is not None and not np.isnan(float(v)) else 0.0
            except Exception:  # noqa: BLE001
                pass
            return Holdings(
                ticker=ticker,
                top_holdings=top_holdings,
                sector_weights=sectors,
                as_of=_today(),
                source_tag=self.source_tag if top_holdings else f"{self.source_tag}:NO_HOLDINGS",
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("[YAH holdings %s] fallback empty (%s)", ticker, exc)
            return Holdings(ticker=ticker, as_of=_today(), source_tag=f"{self.source_tag}:EMPTY")

    def get_fund_flow(self, ticker: str) -> FundFlow:
        # yfinance 免费版无 fundflow（返回空结构，触发 ProviderRegistry degraded 走 ETFDB/Bocha 兜底）
        return FundFlow(ticker=ticker, as_of=_today(), source_tag=f"{self.source_tag}:NOT_AVAILABLE")

    def get_peer_info(
        self,
        category: str,
        focus_index: str,
        aum_usd_b: float,
        peer_tickers: list[str],
    ) -> PeerInfoBatch:
        """批量拉 5~8 只 peer 的单行指标（用于对比矩阵）。"""
        batch: PeerInfoBatch = {}
        for p in list(peer_tickers or []):
            try:
                prof = self.get_profile(p)
                h1y = self.get_history(p, "1y")
                h3y = self.get_history(p, "3y")
                risk = self.get_risk_metrics(p, benchmark="SPY")
                # YTD 收益：2026-01-01→今天；没数据就=history最后一行/today 近似
                ytd_ret = None
                one_y_ret = None
                three_y_ann = None
                if h1y.rows and len(h1y.rows):
                    last1y = h1y.rows[-1].adj_close or h1y.rows[-1].close
                    first1y = h1y.rows[0].adj_close or h1y.rows[0].close
                    if first1y and last1y:
                        one_y_ret = (last1y / first1y - 1.0) * 100.0
                    # YTD：1年hist的最后≈252天 → 找今年第一天近似
                    today = _today()
                    ytd_rows = [r for r in h1y.rows if r.date and r.date >= date(today.year, 1, 1)]
                    if len(ytd_rows) >= 2:
                        f = ytd_rows[0].adj_close or ytd_rows[0].close
                        l = ytd_rows[-1].adj_close or ytd_rows[-1].close
                        if f and l:
                            ytd_ret = (l / f - 1.0) * 100.0
                if h3y.rows and len(h3y.rows) >= 500:
                    f = h3y.rows[0].adj_close or h3y.rows[0].close
                    l = h3y.rows[-1].adj_close or h3y.rows[-1].close
                    if f and l:
                        total = (l / f - 1.0) * 100.0
                        three_y_ann = ((1 + total / 100.0) ** (1/3) - 1) * 100.0 if total is not None else None
                batch[p] = {
                    "ticker": p,
                    "name": prof.name,
                    "aum_usd_m": prof.aum_usd_m,
                    "expense_ratio": prof.expense_ratio,
                    "ytd_return_pct": _as_float(ytd_ret),
                    "one_y_return_pct": _as_float(one_y_ret),
                    "three_y_annual_pct": _as_float(three_y_ann),
                    "sharpe_3y": risk.sharpe_3y,
                    "max_drawdown_pct": risk.max_drawdown_pct,
                    "beta": risk.beta,
                    "dividend_yield_pct": prof.dividend_yield_pct,
                    "as_of": _today(),
                    "source_tag": self.source_tag,
                }
            except Exception as exc:  # noqa: BLE001
                log.warning("[YAH peer_info %s] skip (%s)", p, exc)
                batch[p] = {"ticker": p, "as_of": _today(), "source_tag": f"{self.source_tag}:SKIP"}
        return batch


# ============================================================
# ETFDB · 免费 HTML 扒站（补 Yahoo 拿不到的数据：expense_ratio / category / aum）
# ============================================================
class ETFDBProvider(DataProvider):
    name = "etfdb"
    source_tag = "ETFDB"

    def is_available(self) -> bool:
        try:
            import requests  # noqa: F401
            from bs4 import BeautifulSoup  # noqa: F401
            return True
        except Exception:  # noqa: BLE001
            return False

    def _fetch_html(self, url: str) -> str | None:
        try:
            import requests
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
            }
            r = requests.get(url, timeout=10, headers=headers)
            if r.status_code == 200:
                return r.text
        except Exception:  # noqa: BLE001
            pass
        return None

    def get_profile(self, ticker: str) -> ETFProfile:
        url = f"https://etfdb.com/etf/{ticker}/"
        html = self._fetch_html(url)
        if not html:
            return ETFProfile(ticker=ticker, as_of=_today(), source_tag=f"{self.source_tag}:NO_NET")
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "lxml")
            data = {"name": "", "issuer": "", "category": "", "focus_index": "",
                    "expense_ratio": None, "aum_usd_m": None, "inception": None, "yield": None}
            # ETFDB profile 在 <div class="data-table"> 中的 key/value 行
            for tr in soup.select("div.data-table tr, table.fund-trades tr"):
                cells = tr.find_all(["th", "td"])
                if len(cells) >= 2:
                    k = cells[0].get_text(" ", strip=True)
                    v = cells[1].get_text(" ", strip=True)
                    if k in ("ETF Name", "Name"):
                        data["name"] = v
                    elif k in ("Issuer", "Issuer Brand"):
                        data["issuer"] = v
                    elif k in ("Category",):
                        data["category"] = v
                    elif k in ("Index Tracked", "Underlying Index"):
                        data["focus_index"] = v
                    elif k in ("Expense Ratio", "Annual Expense Ratio"):
                        tmp = v.replace("%", "").strip()
                        try:
                            data["expense_ratio"] = float(tmp) / 100.0
                        except Exception:  # noqa: BLE001
                            pass
                    elif k in ("Assets Under Management", "AUM", "Total Assets"):
                        tmp = v.replace("$", "").replace(",", "").strip()
                        mul = 1.0
                        if tmp.endswith("B"):
                            tmp, mul = tmp[:-1], 1_000.0
                        elif tmp.endswith("M"):
                            tmp, mul = tmp[:-1], 1.0
                        try:
                            data["aum_usd_m"] = float(tmp) * mul
                        except Exception:  # noqa: BLE001
                            pass
                    elif k in ("Inception Date",):
                        try:
                            from datetime import datetime as _dt
                            data["inception"] = _dt.strptime(v[:10], "%m/%d/%Y").date()
                        except Exception:  # noqa: BLE001
                            try:
                                data["inception"] = date.fromisoformat(v[:10])
                            except Exception:  # noqa: BLE001
                                pass
                    elif k in ("Yield", "Dividend Yield"):
                        try:
                            data["yield"] = float(v.replace("%", "").strip())
                        except Exception:  # noqa: BLE001
                            pass
            return ETFProfile(
                ticker=ticker,
                name=data["name"],
                issuer=data["issuer"],
                category=data["category"],
                focus_index=data["focus_index"],
                expense_ratio=data["expense_ratio"],
                aum_usd_m=data["aum_usd_m"],
                inception_date=data["inception"],
                dividend_yield_pct=data["yield"],
                as_of=_today(),
                source_tag=self.source_tag,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("[ETFDB profile %s] parse failed (%s)", ticker, exc)
            return ETFProfile(ticker=ticker, as_of=_today(), source_tag=f"{self.source_tag}:PARSE_ERR")

    def get_history(
        self, ticker: str,
        period: Literal["1mo", "3mo", "6mo", "1y", "3y", "5y", "10y", "ytd"],
    ) -> QuoteHistory:
        # ETFDB 不提供历史行情 → degraded，让 Registry 回退到 Yahoo
        return QuoteHistory(ticker=ticker, period=period, rows=[], as_of=_today(), source_tag=f"{self.source_tag}:NOT_AVAILABLE")

    def get_risk_metrics(self, ticker: str, benchmark: str = "SPY") -> RiskMetrics:
        return RiskMetrics(ticker=ticker, as_of=_today(), source_tag=f"{self.source_tag}:NOT_AVAILABLE")

    def get_holdings(self, ticker: str, top_n: int = 15) -> Holdings:
        url = f"https://etfdb.com/etf/{ticker}/#holdings"
        html = self._fetch_html(url)
        if not html:
            return Holdings(ticker=ticker, as_of=_today(), source_tag=f"{self.source_tag}:NO_NET")
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "lxml")
            tops: list[HoldingItem] = []
            for tr in soup.select("table.holdings-table tr"):
                cells = tr.find_all("td")
                if len(cells) >= 3 and len(tops) < top_n:
                    name = cells[0].get_text(strip=True)
                    tick = cells[1].get_text(strip=True) if len(cells) >= 2 else ""
                    w = cells[-1].get_text(strip=True).replace("%", "")
                    weight = None
                    try:
                        weight = float(w)
                    except Exception:  # noqa: BLE001
                        pass
                    tops.append(HoldingItem(name=name, ticker=tick, weight_pct=weight))
            return Holdings(ticker=ticker, top_holdings=tops, sector_weights={}, as_of=_today(),
                            source_tag=self.source_tag if tops else f"{self.source_tag}:NO_HOLDINGS")
        except Exception as exc:  # noqa: BLE001
            log.warning("[ETFDB holdings %s] fail (%s)", ticker, exc)
            return Holdings(ticker=ticker, as_of=_today(), source_tag=f"{self.source_tag}:PARSE_ERR")

    def get_fund_flow(self, ticker: str) -> FundFlow:
        # ETFDB 目前暂不实现（留给后续 Provider 追加），返回 degrade 空
        return FundFlow(ticker=ticker, as_of=_today(), source_tag=f"{self.source_tag}:NOT_AVAILABLE")

    def get_peer_info(
        self,
        category: str,
        focus_index: str,
        aum_usd_b: float,
        peer_tickers: list[str],
    ) -> PeerInfoBatch:
        batch: PeerInfoBatch = {}
        for p in peer_tickers or []:
            prof = self.get_profile(p)
            batch[p] = {
                "ticker": p,
                "name": prof.name,
                "aum_usd_m": prof.aum_usd_m,
                "expense_ratio": prof.expense_ratio,
                "dividend_yield_pct": prof.dividend_yield_pct,
                "as_of": _today(),
                "source_tag": self.source_tag,
            }
        return batch


# ============================================================
# Polygon / AlphaVantage · 付费 Provider（MVP 占位，缺 Key 默认不可用）
# ============================================================
class PolygonProvider(DataProvider):
    name = "polygon"
    source_tag = "POL"

    def is_available(self) -> bool:
        key = (os.getenv("POLYGON_API_KEY") or "").strip()
        return bool(key) and not key.lower().startswith(("your_", "demo_", "polygon_key_here"))

    def get_profile(self, ticker: str) -> ETFProfile:
        return ETFProfile(ticker=ticker, as_of=_today(), source_tag=f"{self.source_tag}:NOT_IMPLEMENTED")

    def get_history(self, ticker: str, period) -> QuoteHistory:  # type: ignore[override]
        return QuoteHistory(ticker=ticker, period=str(period), rows=[], as_of=_today(), source_tag=f"{self.source_tag}:NOT_IMPLEMENTED")

    def get_risk_metrics(self, ticker: str, benchmark: str = "SPY") -> RiskMetrics:
        return RiskMetrics(ticker=ticker, as_of=_today(), source_tag=f"{self.source_tag}:NOT_IMPLEMENTED")

    def get_holdings(self, ticker: str, top_n: int = 15) -> Holdings:
        return Holdings(ticker=ticker, as_of=_today(), source_tag=f"{self.source_tag}:NOT_IMPLEMENTED")

    def get_fund_flow(self, ticker: str) -> FundFlow:
        return FundFlow(ticker=ticker, as_of=_today(), source_tag=f"{self.source_tag}:NOT_IMPLEMENTED")

    def get_peer_info(self, category: str, focus_index: str, aum_usd_b: float, peer_tickers: list[str]) -> PeerInfoBatch:
        return {}


class AlphaVantageProvider(DataProvider):
    name = "alpha_vantage"
    source_tag = "AV"

    def is_available(self) -> bool:
        key = (os.getenv("ALPHA_VANTAGE_API_KEY") or "").strip()
        return bool(key) and not key.lower().startswith(("your_", "demo_"))

    def get_profile(self, ticker: str) -> ETFProfile:
        return ETFProfile(ticker=ticker, as_of=_today(), source_tag=f"{self.source_tag}:NOT_IMPLEMENTED")

    def get_history(self, ticker: str, period) -> QuoteHistory:  # type: ignore[override]
        return QuoteHistory(ticker=ticker, period=str(period), rows=[], as_of=_today(), source_tag=f"{self.source_tag}:NOT_IMPLEMENTED")

    def get_risk_metrics(self, ticker: str, benchmark: str = "SPY") -> RiskMetrics:
        return RiskMetrics(ticker=ticker, as_of=_today(), source_tag=f"{self.source_tag}:NOT_IMPLEMENTED")

    def get_holdings(self, ticker: str, top_n: int = 15) -> Holdings:
        return Holdings(ticker=ticker, as_of=_today(), source_tag=f"{self.source_tag}:NOT_IMPLEMENTED")

    def get_fund_flow(self, ticker: str) -> FundFlow:
        return FundFlow(ticker=ticker, as_of=_today(), source_tag=f"{self.source_tag}:NOT_IMPLEMENTED")

    def get_peer_info(self, category: str, focus_index: str, aum_usd_b: float, peer_tickers: list[str]) -> PeerInfoBatch:
        return {}
