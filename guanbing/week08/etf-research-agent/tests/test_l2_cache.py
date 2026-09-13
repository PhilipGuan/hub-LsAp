# -*- coding: utf-8 -*-
"""L2 · ResultCache TTL 24h / 空值 1h（无网 · 纯文件 I/O）。"""
from __future__ import annotations

import json
import time
from datetime import date, timedelta
from pathlib import Path

import pytest

from backend.cache.result_cache import ResultCache


class TestL2ResultCache:
    def test_24h_hit_and_ttl_expired_miss(self, tmp_path: Path) -> None:
        cache = ResultCache(cache_dir=tmp_path)
        today = date.today()
        payload = {"a": 1, "b": ["x", "y"]}
        cache.set("Soxl", "get_profile", payload, today=today)
        hit, got = cache.get("SOXL", "get_profile", today=today)
        assert hit is True
        assert got == payload

        # 篡改 _meta.ttl_until_ts 为过去 → miss
        (key_name,) = [p.stem for p in tmp_path.glob("*.json")]
        fp = tmp_path / f"{key_name}.json"
        data = json.loads(fp.read_text(encoding="utf-8"))
        data["_meta"]["ttl_until_ts"] = time.time() - 10
        fp.write_text(json.dumps(data), encoding="utf-8")
        hit2, got2 = cache.get("SOXL", "get_profile", today=today)
        assert hit2 is False
        assert got2 is None

    def test_empty_payload_1h_placeholder_hit(self, tmp_path: Path) -> None:
        cache = ResultCache(cache_dir=tmp_path)
        today = date.today()
        cache.set("SOXL", "get_fund_flow", None, today=today)
        hit, got = cache.get("SOXL", "get_fund_flow", today=today)
        assert hit is True
        assert got is None

        (key_name,) = [p.stem for p in tmp_path.glob("*.json")]
        fp = tmp_path / f"{key_name}.json"
        meta = json.loads(fp.read_text(encoding="utf-8"))["_meta"]
        assert meta["is_empty"] is True
        assert meta["ttl_hours"] == 1

    def test_different_args_hash_different_keys(self, tmp_path: Path) -> None:
        cache = ResultCache(cache_dir=tmp_path)
        today = date.today()
        cache.set("SOXL", "get_history", {"a": 1}, today=today, extra_args={"period": "1y"})
        cache.set("SOXL", "get_history", {"b": 2}, today=today, extra_args={"period": "3y"})
        assert len(list(tmp_path.glob("*.json"))) == 2

    def test_missing_key_returns_false_none(self, tmp_path: Path) -> None:
        cache = ResultCache(cache_dir=tmp_path)
        hit, got = cache.get("NOPE", "nothing", today=date.today())
        assert hit is False
        assert got is None
