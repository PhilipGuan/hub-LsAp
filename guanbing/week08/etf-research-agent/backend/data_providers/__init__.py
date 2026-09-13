# -*- coding: utf-8 -*-
"""M1 插件式 Provider 包。"""
from __future__ import annotations

from .base import DataProvider
from .registry import ProviderRegistry
from .implementations import (
    YahooFinanceProvider,
    ETFDBProvider,
    PolygonProvider,
    AlphaVantageProvider,
)

__all__ = [
    "DataProvider",
    "ProviderRegistry",
    "YahooFinanceProvider",
    "ETFDBProvider",
    "PolygonProvider",
    "AlphaVantageProvider",
]
