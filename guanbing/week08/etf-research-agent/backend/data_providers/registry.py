# -*- coding: utf-8 -*-
"""ProviderRegistry（Dry-Run 骨架 · Spec §6 M1 Registry 行为）
按优先级串联 4 个 Provider；fetch_first_available 逐个尝试直到成功。
正式实现时逐个 Provider 实例化 + Key 检查补齐即可，接口不变。"""
from __future__ import annotations

import logging
from typing import Any

from .base import DataProvider

logger = logging.getLogger(__name__)

_PRIORITY_MAP: dict[str, int] = {
    "polygon": 0,
    "alpha_vantage": 1,
    "yahoo_finance": 2,
    "etfdb": 3,
}


class ProviderRegistry:
    def __init__(self, providers: list[DataProvider] | None = None) -> None:
        self.providers: list[DataProvider] = providers or []
        self._filter_available()
        self._sort_providers()

    def _filter_available(self) -> None:
        self.providers = [p for p in self.providers if p.is_available()]

    def _sort_providers(self) -> None:
        self.providers.sort(key=lambda x: _PRIORITY_MAP.get(x.name, 99))

    def available_providers(self) -> list[str]:
        return [p.name for p in self.providers]

    def add_provider(self, p: DataProvider) -> None:
        if p.is_available():
            self.providers.append(p)
            self._sort_providers()

    def fetch_first_available(
        self,
        method_name: str,
        ticker: str,
        *args: Any,
        **kwargs: Any,
    ) -> tuple[Any, str, bool]:
        """按优先级逐个 Provider 调用 method_name。

        Returns:
            (result_object, provider_name, is_degraded)
            degraded=True 代表所有 Provider 失败，调用方需要走 Bocha 搜索兜底。
        """
        last_err: Exception | None = None
        for p in self.providers:
            try:
                method = getattr(p, method_name, None)
                if not callable(method):
                    raise AttributeError(f"Provider {p.name} 未实现 {method_name}")
                result = method(ticker, *args, **kwargs)
                logger.info(
                    "[Registry] %s.%s(ticker=%s) SUCCESS",
                    p.name, method_name, ticker,
                )
                return result, p.name, False
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                logger.warning(
                    "[Registry] %s.%s(ticker=%s) FAILED: %s",
                    p.name, method_name, ticker, exc,
                )
                continue
        logger.error(
            "[Registry] All providers failed for %s.%s(ticker=%s): last_err=%s",
            method_name, method_name, ticker, last_err,
        )
        return {}, f"NONE_AVAILABLE ({last_err!s})", True
