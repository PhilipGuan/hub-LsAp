# -*- coding: utf-8 -*-
"""L2 · Agent / Engine / Storage / Templates（无网 · 全 mock · Spec §8 L2）。

硬约束：
- 不调真实 LLM（BaseAgent._llm_chat / generate 全 monkeypatch 或只测 parse_response/render_prompt）
- 不碰真实网络（BochaSearch 无 Key 返回 []）
- 不写真实磁盘数据（tmp_data_dir fixture）
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pytest

from backend import models
from backend.agent.agents import (
    BochaSearch,
    ETFResearchEngine,
    JudgeAgent,
    KeywordAgent,
    PlanningAgent,
    ReportAgent,
    SummaryAgent,
)
from backend.agent.base import BaseAgent
from backend.cache.result_cache import ResultCache
from backend.data_providers.base import DataProvider
from backend.exporter.report_exporter import ReportExporter
from backend.storage import Storage


# ============================================================
# 1. BaseAgent render_prompt：5 子 Agent 模板能加载，style snippet include 不崩
# ============================================================
class TestL2BaseAgentRenderPrompt:
    @pytest.mark.parametrize(
        "agent_cls,ctx_keys,keyword_in_output",
        [
            (PlanningAgent, {"ticker": "SOXL", "missing_sections": ["fundamentals"],
                             "section_titles": {"fundamentals": "1. Fundamentals"},
                             "peer_matrix_summary": "main=SOXL n=5", "style": "analyst",
                             "profile": None, "stale_warnings": [],
                             "expected_output": "plan JSON"},
             ["SOXL", "fundamentals", "plan"]),
            (KeywordAgent, {"ticker": "SOXL", "section_id": "risk",
                            "section_title": "Risk Metrics", "style": "institutional",
                            "existing_keywords": ["beta", "volatility"],
                            "expected_output": "keywords JSON"},
             ["SOXL", "risk", "keywords"]),
            (SummaryAgent, {"ticker": "SOXL", "section_id": "holdings",
                            "section_title": "Top Holdings", "bocha_results": [],
                            "existing_content": "", "style": "explainer",
                            "expected_output": "content + citations"},
             ["SOXL", "holdings", "content"]),
            (JudgeAgent, {"ticker": "SOXL",
                          "draft_sections": [{"section_id": "fundamentals", "plain_text": "OK", "citations": []}],
                          "section_ids_expected": ["fundamentals"],
                          "filled_ids": ["fundamentals"],
                          "missing_ids": [],
                          "draft_preview": [{"section_id": "fundamentals", "chars": 2, "citations": 0, "preview": "OK"}],
                          "stale_warnings": [], "style": "analyst",
                          "expected_output": "converged JSON"},
             ["SOXL", "converged", "已完成章节"]),
            (ReportAgent, {"ticker": "SOXL",
                           "filled_sections": [],
                           "peer_matrix": None,
                           "analyst_tags": ["bullish", "AI demand"],
                           "stale_warnings": [],
                           "style": "analyst",
                           "expected_output": "sections_overview JSON"},
             ["SOXL", "sections_overview", "analyst tags"]),
        ],
    )
    def test_5_agent_templates_render_without_error(
        self, agent_cls: type[BaseAgent], ctx_keys: dict, keyword_in_output: list[str]
    ) -> None:
        agent = agent_cls()
        ctx_full = dict(ctx_keys)
        # 缺 template 就应该 raise TemplateNotFound；只要不抛就是模板路径正确
        try:
            rendered = agent.render_prompt(ctx_full)
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"{agent_cls.__name__}.render_prompt 抛错：{exc!s}")
            return
        assert isinstance(rendered, str) and len(rendered) > 20, (
            f"{agent_cls.__name__} 渲染内容过短 len={len(rendered)}"
        )
        for kw in keyword_in_output:
            # 允许大小写不敏感匹配（Jinja2 变量渲染可能含大小写差异）
            low = rendered.lower()
            assert kw.lower() in low, (
                f"{agent_cls.__name__} 输出中缺少关键字 {kw!r}（preview={rendered[:200]!r}）"
            )

    def test_style_snippet_include_missing_does_not_break(self) -> None:
        """即使 snippet 不存在（或路径不对），{% include ... ignore missing %} 也不该抛错。"""
        a = PlanningAgent()
        ctx = {
            "ticker": "SOXL", "missing_sections": ["fundamentals"],
            "section_titles": {"fundamentals": "1. Fundamentals"},
            "peer_matrix_summary": "", "style": "__nonexistent_style__",
            "profile": None, "stale_warnings": [],
            "expected_output": "plan",
        }
        # 不应抛 TemplateNotFound
        out = a.render_prompt(ctx)
        assert len(out) > 10


# ============================================================
# 2. PlanningAgent.parse_response：fence JSON → list[dict]，section_id 顺序保持
# ============================================================
class TestL2PlanningParse:
    def test_parse_json_fence_list_ordered(self) -> None:
        raw = """
Here is the plan:
```json
{
  "plan": [
    {"section_id": "fundamentals", "section_title": "1. Fundamentals",
     "keywords": ["aum","expense"], "bocha_queries": ["SOXL aum 2026"]},
    {"section_id": "risk", "section_title": "Risk",
     "keywords": ["beta"], "bocha_queries": []},
    {"section_id": "thesis_conclusion", "section_title": "Conclusion",
     "keywords": [], "bocha_queries": []}
  ]
}
```
"""
        plan = PlanningAgent().parse_response(raw)
        assert isinstance(plan, list) and len(plan) == 3
        assert [p["section_id"] for p in plan] == ["fundamentals", "risk", "thesis_conclusion"]
        assert plan[0]["keywords"] == ["aum", "expense"]
        assert plan[0]["bocha_queries"] == ["SOXL aum 2026"]

    def test_parse_no_fence_but_json_object_works(self) -> None:
        raw = '{"plan":[{"section_id":"holdings","section_title":"Holdings","keywords":["NVDA"],"bocha_queries":[]}]}'
        plan = PlanningAgent().parse_response(raw)
        assert len(plan) == 1 and plan[0]["section_id"] == "holdings"

    def test_parse_empty_plan_raises(self) -> None:
        with pytest.raises(ValueError):
            PlanningAgent().parse_response('{"plan":[]}')


# ============================================================
# 3. KeywordAgent.parse_response：去重 + ≥3 校验 + 15 截断
# ============================================================
class TestL2KeywordParse:
    def test_dedup_and_bounds_3_to_15(self) -> None:
        raw = """```json
{"keywords": ["beta", "volatility", "drawdown", "Beta", "VOLATILITY", "Sharpe",
              "a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k"]}
```"""
        kws = KeywordAgent().parse_response(raw)
        # beta/Beta 去重，volatility/VOLATILITY 去重
        assert kws.count("beta") + kws.count("Beta") + kws.count("BETA") == 1
        assert len(kws) <= 15
        # 最少保留原 list 前 15 个不重复
        assert len(kws) >= 3

    def test_less_than_3_unique_raises(self) -> None:
        with pytest.raises(ValueError):
            KeywordAgent().parse_response('{"keywords": ["x", "X", "x", "Y"]}')

    def test_no_fence_object_only(self) -> None:
        raw = '{"keywords":["k1","k2","k3","k4"]}'
        assert KeywordAgent().parse_response(raw) == ["k1", "k2", "k3", "k4"]


# ============================================================
# 4. Storage roundtrip：new_record → save → get；index.jsonl 存在；export_paths 7 键
# ============================================================
class TestL2StorageRoundtrip:
    def test_new_save_get_roundtrip_fields_and_index(self, tmp_path: Path) -> None:
        s = Storage(data_dir=tmp_path / "data")
        rec = s.new_record(ticker="soxl", style="analyst", peers=5)
        assert rec.research_id.startswith("R_")
        assert rec.ticker == "SOXL"
        assert rec.status == "pending"
        assert rec.style == "analyst"
        assert 5 <= rec.peers_requested <= 8
        assert s.index_path.exists(), "new_record 应立即写 index.jsonl"
        assert s.record_json_path(rec.research_id).exists()

        # 更新：status=running + process 添加 step
        rec.status = "running"
        rec.process.steps.append(models.StepLog(type="phase", message="Phase0 ok"))
        s.save(rec)

        loaded = s.get(rec.research_id)
        assert loaded is not None
        assert loaded.research_id == rec.research_id
        assert loaded.ticker == "SOXL"
        assert loaded.status == "running"
        assert len(loaded.process.steps) == 1
        assert loaded.process.steps[0].message == "Phase0 ok"

        # list 返回
        listed = s.list(limit=10)
        assert any(r.research_id == rec.research_id for r in listed)

    def test_export_paths_has_7_keys(self, tmp_path: Path) -> None:
        s = Storage(data_dir=tmp_path / "data2")
        paths = s.export_paths("R_FAKE_ID")
        assert set(paths.keys()) == {"json", "md", "html", "profile_csv", "history_csv", "holdings_csv", "peer_csv"}
        for p in paths.values():
            assert isinstance(p, Path)
            assert p.parent.name == "exports"


# ============================================================
# 5. Engine.run：Provider 全 degrade + 5 Agent 全抛异常 → 仍能 status=completed + export_paths 7
# ============================================================
class _AllDegradeProvider(DataProvider):
    provider_name = "ALWAYS_DEGRADE"
    name = "always_degrade"
    source_tag = "DEG"

    def is_available(self) -> bool:
        return True

    def get_profile(self, ticker: str):
        return None

    def get_history(self, ticker: str, period: str):
        return None

    def get_risk_metrics(self, ticker: str, benchmark: str):
        return None

    def get_holdings(self, ticker: str, top_n: int = 15):
        return None

    def get_fund_flow(self, ticker: str):
        return None

    def get_peer_info(
        self, category: str, focus_index: str, aum_usd_b: float, peer_tickers: list
    ):
        return models.PeerInfoBatch(rows=[])


class TestL2EngineFullDegrade:
    def test_all_provider_degrade_then_agents_fail_but_still_completed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        data_dir = tmp_path / "storage"
        cache_dir = tmp_path / "cache"
        data_dir.mkdir()
        cache_dir.mkdir()

        storage = Storage(data_dir=data_dir)
        cache = ResultCache(cache_dir)
        exporter = ReportExporter()

        engine = ETFResearchEngine(
            storage=storage,
            providers=[_AllDegradeProvider()],
            cache=cache,
            exporter=exporter,
        )

        # 全 mock 掉 5 Agent.generate：永远抛 RuntimeError，测试 degrade 兜底路径
        def _boom(*args, **kwargs):  # noqa: D401
            raise RuntimeError("LLM call disabled in L2 offline test")

        monkeypatch.setattr(PlanningAgent, "generate", _boom)
        monkeypatch.setattr(KeywordAgent, "generate", _boom)
        monkeypatch.setattr(SummaryAgent, "generate", _boom)
        monkeypatch.setattr(JudgeAgent, "generate", _boom)
        monkeypatch.setattr(ReportAgent, "generate", _boom)

        rid = "R_ENGINE_DEGRADE_001"
        rec = engine.run(research_id=rid, ticker="SOXL", style="analyst", peers=5)

        # ---- 门禁 A：status=completed（绝不能是 failed/pending/running）----
        assert rec.status == "completed", (
            f"Engine 全 degrade 后状态应为 completed，实际 {rec.status!r}; error={rec.error!r}"
        )
        assert rec.research_id == rid
        assert rec.ticker == "SOXL"

        # ---- 门禁 B：report / peer_matrix / sections 全有 ----
        assert rec.report is not None
        assert rec.report.ticker == "SOXL"
        assert len(rec.report.sections) >= 10, (
            f"兜底段应填 10 节，实际 {len(rec.report.sections)}"
        )
        # 10 section_ids 全覆盖
        filled_ids = {cp.section_id for cp in rec.report.sections}
        from backend.schemas.etf_schema import ETFSchema
        all_ids = set(ETFSchema().section_ids)
        missing_expected = all_ids - filled_ids
        assert not missing_expected, f"缺少兜底节：{missing_expected!r}"

        # ---- 门禁 C：Confidence 存在 + AnalystRating 存在 ----
        assert rec.report.confidence is not None
        assert rec.report.confidence.overall in {"low", "medium", "high"}
        assert rec.report.analyst_rating is not None
        assert rec.report.analyst_rating.rating in {"bullish", "bearish", "neutral"}

        # ---- 门禁 D：PeerMatrix（即使全 degrade PeerResolver 8 大桶兜底也应 >0 rows）----
        assert rec.report.peer_matrix is not None
        assert 5 <= len(rec.report.peer_matrix.rows) <= 8, (
            f"PeerMatrix 行数应为 5~8，实际 {len(rec.report.peer_matrix.rows)}"
        )

        # ---- 门禁 E：Storage export_paths 7 路径全部可由 storage.export_paths 生成 ----
        paths = storage.export_paths(rid)
        assert len(paths) == 7
        # 目录必须已在 save 时创建（new_record / save 均会 _record_dir 创建 exports 父）
        for k, p in paths.items():
            assert p.parent.exists(), f"exports dir 未创建：key={k} path={p}"


# ============================================================
# 6. BochaSearch 无 Key / 占位 Key 返回 []；is_available=False
# ============================================================
class TestL2BochaSearchOffline:
    def test_empty_key_not_available_and_search_empty(self) -> None:
        b = BochaSearch(api_key="", base_url="https://fake.local")
        assert b.is_available() is False
        assert b.search("anything", count=3) == []

    @pytest.mark.parametrize("bad", ["your_api_key_here", "your_BOCHA_123", "sk-xxx-replace-me", "YOUR KEY HERE"])
    def test_placeholder_key_not_available(self, bad: str) -> None:
        b = BochaSearch(api_key=bad)
        assert b.is_available() is False, f"占位 Key 不应可用：{bad!r}"
        assert b.search("x") == []
