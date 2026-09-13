# -*- coding: utf-8 -*-
"""DataProvider 抽象基类（Dry-Run 骨架 · 7 方法契约对齐 Spec §6 M1）
正式实现时逐个方法替换 raise NotImplementedError 为真实调用。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal

from ..models import (
    ETFProfile,
    FundFlow,
    Holdings,
    PeerInfoBatch,
    QuoteHistory,
    RiskMetrics,
)


class DataProvider(ABC):
    """所有结构化数据源统一契约（Spec §6 M1 DataProvider 抽象）。"""

    name: str = "base"
    source_tag: str = "BASE"

    # --------- 可用性 ---------
    @abstractmethod
    def is_available(self) -> bool:
        """Key/环境是否可用（付费 Provider 需检查 env 是否有 Key）。"""
        raise NotImplementedError

    # --------- 基础信息 ---------
    @abstractmethod
    def get_profile(self, ticker: str) -> ETFProfile:
        raise NotImplementedError

    # --------- 行情历史 ---------
    @abstractmethod
    def get_history(
        self,
        ticker: str,
        period: Literal["1mo", "3mo", "6mo", "1y", "3y", "5y", "10y", "ytd"],
    ) -> QuoteHistory:
        raise NotImplementedError

    # --------- 风险指标 ---------
    @abstractmethod
    def get_risk_metrics(self, ticker: str, benchmark: str = "SPY") -> RiskMetrics:
        raise NotImplementedError

    # --------- 持仓 ---------
    @abstractmethod
    def get_holdings(self, ticker: str, top_n: int = 15) -> Holdings:
        raise NotImplementedError

    # --------- 资金流 ---------
    @abstractmethod
    def get_fund_flow(self, ticker: str) -> FundFlow:
        raise NotImplementedError

    # --------- 同类批量 ---------
    @abstractmethod
    def get_peer_info(
        self,
        category: str,
        focus_index: str,
        aum_usd_b: float,
        peer_tickers: list[str],
    ) -> PeerInfoBatch:
        """批量拉取 peer_tickers 这批对标 ETF 的单行指标数据用于对比矩阵。"""
        raise NotImplementedError
