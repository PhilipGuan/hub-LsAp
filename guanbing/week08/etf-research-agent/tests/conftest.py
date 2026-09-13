# -*- coding: utf-8 -*-
"""pytest fixtures（L1/L2 无网用 · 隔离真实 Week08/.env 读）。"""
from __future__ import annotations

import importlib
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Generator

import pytest
from fastapi.testclient import TestClient

from backend import models


ENV_KEYS_TO_CLEAR = [
    "LLM_PROVIDER",
    "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL",
    "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL",
    "QWEN_API_KEY", "QWEN_BASE_URL", "QWEN_MODEL",
    "BOCHA_API_KEY", "BOCHA_BASE_URL",
    "LLM_TEMPERATURE", "RESEARCH_MAX_ROUNDS",
    "FRONTEND_ORIGIN",
]


@pytest.fixture(autouse=True)
def mock_env(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    """每个用例清空所有 envify 环境变量，防止读 Week08/.env 污染（autouse 全测）。

    关键修复：mock 掉 dotenv.load_dotenv 防止它读磁盘上真实的 Week08/.env override=True 覆盖 monkeypatch 的环境。
    """
    for k in ENV_KEYS_TO_CLEAR:
        monkeypatch.delenv(k, raising=False)
    # 核心：禁用磁盘级 load_dotenv，完全靠 monkeypatch.setenv 控制环境变量
    def _fake_load_dotenv(*_args, **_kwargs):
        return False
    try:
        import dotenv as _dotenv
        monkeypatch.setattr(_dotenv, "load_dotenv", _fake_load_dotenv)
    except Exception:  # noqa: BLE001
        pass
    # 把 backend.config 的模块级 LLM_PROVIDER/_LLM_API_KEY 缓存强制下次用 importlib.reload 重新读
    if "backend.config" in sys.modules:
        del sys.modules["backend.config"]
    yield


@pytest.fixture()
def fake_week08_env(tmp_path: Path, request: Any) -> Path:
    """工厂式 fixture：写 .env 内容到 tmp_path/Week08.env，返回路径。

    使用方式：
        @pytest.mark.parametrize("fake_week08_env", [\"\"\"...\"\"\"], indirect=True)
        def test_xxx(fake_week08_env):
            ...  # fake_week08_env 是 Path 对象
    """
    content = getattr(request, "param", "") or ""
    p = tmp_path / "Week08" / ".env"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


@pytest.fixture()
def fake_local_env(tmp_path: Path) -> Path:
    """写本目录 .env（用于测试级联优先级）。"""
    p = tmp_path / "etf-research-agent" / ".env"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


@pytest.fixture()
def tmp_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """覆盖 config.DATA_DIR / CACHE_DIR 到 tmp_path，测试 Storage/Exporter 不污染真实数据。"""
    data_dir = tmp_path / "data" / "etf_records"
    cache_dir = tmp_path / "cache"
    data_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    # 通过 monkeypatch 环境变量生效（config.py 里是模块级赋值；需要 import config 之前先设置 os.environ DATA_DIR/CACHE_DIR？
    # 为了简单，直接 monkeypatch.setattr 模块变量。
    monkeypatch.setenv("ETF_TEST_DATA_DIR", str(data_dir))
    monkeypatch.setenv("ETF_TEST_CACHE_DIR", str(cache_dir))
    return tmp_path


@pytest.fixture()
def fake_research_record() -> models.ResearchRecord:
    """工厂：一份 completed 状态的 SOXL 假 ResearchRecord（含 7 节 / 中等置信度）。"""
    now = datetime.utcnow()
    return models.ResearchRecord(
        research_id="R_20260913_234108_SOXL_fake",
        ticker="SOXL",
        status="completed",
        created_at=now,
        updated_at=now,
        style="analyst",
        peers_requested=5,
        report=models.ReportContent(
            ticker="SOXL",
            report_date=date(2026, 9, 13),
            style="analyst",
            sections=[
                models.CitedParagraph(section_id="fundamentals", plain_text="SOXL 是 Direxion 发行的 3× 半导体 ETF。"),
                models.CitedParagraph(section_id="performance_1y", plain_text="近 1 年上涨 65%，最大回撤 -38%。"),
            ],
            peer_matrix=models.PeerComparisonMatrix(
                main_ticker="SOXL",
                rows=[
                    models.PeerInfoRow(ticker="SOXL", name="Direxion Daily Semiconductor Bull 3X Shares", aum_usd_m=8500.0, expense_ratio=0.0094,
                                       ytd_return_pct=52.0, one_y_return_pct=65.0, sharpe_3y=1.2, max_drawdown_pct=38.0,
                                       beta=1.6, dividend_yield_pct=0.25, as_of=date(2026, 9, 10), source_tag="YAH"),
                ],
                as_of=date(2026, 9, 10),
            ),
            analyst_rating=models.AnalystRating(rating="bullish", key_drivers=["AI GPU 需求", "行业 beta 高"], key_risks=["杠杆损耗", "加息冲击估值"]),
            stale_warnings=["aum_usd_m 滞后 40d（>30d）"],
            confidence=models.Confidence(overall="medium", info_cutoff=date(2026, 9, 10), notes=["Bocha 缺 Key，未补抓催化剂节"]),
            process=models.ProcessLog(
                plan=["profile", "history", "risk", "holdings", "fundflow", "peer_matrix"],
                search_queries=["SOXL expense ratio 2026 site:etfdb.com"],
                reviewed_urls=["https://etfdb.com/etf/SOXL/"],
                iterations=3,
                steps=[models.StepLog(type="phase", message="Phase1 Provider 批量完成")],
            ),
            disclaimer=models.Disclaimer(version="v1.0", generated_at=now),
        ),
        sources=[],
        draft=[],
        process=models.ProcessLog(),
    )


@pytest.fixture()
def fastapi_client(monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    """FastAPI TestClient（测试 B3 拦截用）。"""
    # 重 import backend.app（mock_env 已清空变量；mock 环境后才 import，保证 config 模块加载不会用真实环境）
    if "backend.app" in sys.modules:
        del sys.modules["backend.app"]
    # 需要保证 config 能加载 → 填一个非占位的假 Key
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-fake_valid_not_a_placeholder_123")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
    from backend.app import app as _app  # noqa: E402
    with TestClient(_app) as client:
        yield client
