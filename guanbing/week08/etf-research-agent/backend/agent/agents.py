# -*- coding: utf-8 -*-
"""M6：5 个 Agent + Engine + Service（正式实现）。
Spec 约束：
- 所有 parse_response 输出只传 backend.models.py 的 Pydantic 对象；
- MAX_ROUNDS 走 BaseAgent.generate 默认；
- 失败抛 RuntimeError（上层 degrade）。
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

import httpx

from ..cache.result_cache import ResultCache
from ..citations.parser import CitationParser, FreshnessValidator
from ..config import (
    BOCHA_API_KEY,
    BOCHA_BASE_URL,
    CACHE_DIR,
    DATA_DIR,
)
from ..data_providers.base import DataProvider
from ..data_providers.implementations import (
    AlphaVantageProvider,
    ETFDBProvider,
    PolygonProvider,
    YahooFinanceProvider,
)
from ..data_providers.registry import ProviderRegistry
from ..exporter.report_exporter import ReportExporter
from ..models import (
    AnalystRating,
    CitedParagraph,
    Citation,
    Confidence,
    Disclaimer,
    ETFProfile,
    FundFlow,
    Holdings,
    PeerComparisonMatrix,
    PeerInfoRow,
    PeerResolution,
    ProcessLog,
    QuoteHistory,
    ReportContent,
    ResearchRecord,
    RiskMetrics,
    StepLog,
)
from ..schemas.etf_schema import ETFSchema, PeerResolver
from ..storage import Storage
from .base import BaseAgent, MAX_ROUNDS, ProgressCb

logger = logging.getLogger(__name__)

_JSON_FENCE_RE = re.compile(
    r"```(?:json)?\s*([\s\S]*?)\s*```",
    re.IGNORECASE,
)


def _extract_json_block(raw: str) -> str:
    """从 LLM 返回中取第一个 ```json ... ``` 或整段兜底。"""
    if not raw:
        return "{}"
    m = _JSON_FENCE_RE.search(raw)
    if m:
        return m.group(1).strip()
    # 兜底：返回原文本；JSONDecode 交给调用方
    return raw.strip()


def _safe_json_loads(raw: str) -> Any:
    try:
        return json.loads(_extract_json_block(raw))
    except json.JSONDecodeError:
        # 再兜底：取第一个 { 到最后一个 } 的切片
        s = raw.find("{")
        e = raw.rfind("}")
        if s >= 0 and e > s:
            return json.loads(raw[s : e + 1])
        raise


# ============================================================
# 1) Planning Agent
# ============================================================
class PlanningAgent(BaseAgent):
    agent_name = "planning"
    template_path = "planning.jinja2"

    def build_context(  # type: ignore[override]
        self,
        ticker: str,
        missing_sections: list[str],
        peer_matrix_summary: str,
        style: str,
        profile: ETFProfile | None = None,
        stale_warnings: list[str] | None = None,
    ) -> dict[str, Any]:
        schema = ETFSchema()
        titles: dict[str, str] = {sid: schema.section_id_to_title(sid) for sid in missing_sections}
        return {
            "ticker": ticker,
            "missing_sections": missing_sections,
            "section_titles": titles,
            "peer_matrix_summary": peer_matrix_summary or "（无）",
            "style": style,
            "profile": profile.model_dump(mode="json") if profile else None,
            "stale_warnings": stale_warnings or [],
            "expected_output": "JSON { plan: Array<{section_id, section_title, keywords: Array<str>, bocha_queries: Array<str>}> }",
        }

    def parse_response(self, raw: str) -> list[dict[str, Any]]:  # type: ignore[override]
        data = _safe_json_loads(raw)
        plan: Any = data.get("plan") if isinstance(data, dict) else data
        if not isinstance(plan, list):
            raise ValueError(f"PlanningAgent 计划格式错误：需要 list，实际 {type(plan)!r}")
        out: list[dict[str, Any]] = []
        for item in plan:
            if not isinstance(item, dict):
                continue
            sid = str(item.get("section_id") or "").strip()
            if not sid:
                continue
            out.append(
                {
                    "section_id": sid,
                    "section_title": str(item.get("section_title") or sid),
                    "keywords": [str(k) for k in (item.get("keywords") or []) if str(k).strip()],
                    "bocha_queries": [str(q) for q in (item.get("bocha_queries") or []) if str(q).strip()],
                }
            )
        if not out:
            raise ValueError("PlanningAgent 返回 plan 为空")
        return out


# ============================================================
# 2) Keyword Agent（Section 维度关键词矩阵扩展）
# ============================================================
class KeywordAgent(BaseAgent):
    agent_name = "keyword"
    template_path = "keyword.jinja2"

    def build_context(  # type: ignore[override]
        self,
        ticker: str,
        section_id: str,
        section_title: str,
        style: str,
        existing_keywords: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "ticker": ticker,
            "section_id": section_id,
            "section_title": section_title,
            "style": style,
            "existing_keywords": existing_keywords or [],
            "expected_output": "JSON { keywords: Array<str> } 建议 5~10 个，中英文混合允许。",
        }

    def parse_response(self, raw: str) -> list[str]:  # type: ignore[override]
        data = _safe_json_loads(raw)
        kws: Any = data.get("keywords") if isinstance(data, dict) else data
        if not isinstance(kws, list):
            raise ValueError(f"KeywordAgent 返回格式错误：需要 list，实际 {type(kws)!r}")
        cleaned: list[str] = []
        seen: set[str] = set()
        for kw in kws:
            s = str(kw).strip()
            if not s:
                continue
            key = s.lower()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(s)
        if len(cleaned) < 3:
            raise ValueError(f"KeywordAgent 关键词不足 3 个：{cleaned!r}")
        return cleaned[:15]


# ============================================================
# 3) Summary Agent（Bocha 搜索结果 + 已有内容合成 Section 正文）
# ============================================================
class SummaryAgent(BaseAgent):
    agent_name = "summary"
    template_path = "summary.jinja2"

    def build_context(  # type: ignore[override]
        self,
        ticker: str,
        section_id: str,
        section_title: str,
        bocha_results: list[dict[str, Any]],
        existing_content: str,
        style: str,
        provider_tag: str = "BOCHA",
    ) -> dict[str, Any]:
        return {
            "ticker": ticker,
            "section_id": section_id,
            "section_title": section_title,
            "style": style,
            "existing_content": existing_content or "",
            "bocha_results": bocha_results or [],
            "provider_tag": provider_tag,
            "expected_output": (
                "JSON { content: str, citations: Array<{provider, ref_index, source_url?, snippet?, as_of?}> }。"
                "正文中用 [PROV-N] 标记引用，PROV ∈ {YAH, ETFDB, POL, AV, BOCHA}。"
            ),
        }

    def parse_response(self, raw: str) -> dict[str, Any]:  # type: ignore[override]
        data = _safe_json_loads(raw)
        if not isinstance(data, dict):
            raise ValueError(f"SummaryAgent 需要 JSON object，实际 {type(data)!r}")
        content = str(data.get("content") or "").strip()
        if len(content) < 40:
            raise ValueError(f"SummaryAgent content 过短：{content!r}")
        raw_cits = data.get("citations") or []
        citations: list[Citation] = []
        seen: set[tuple[str, int]] = set()
        if isinstance(raw_cits, list):
            for c in raw_cits:
                if not isinstance(c, dict):
                    continue
                try:
                    provider = str(c.get("provider") or "BOCHA").upper()
                    ref = int(c.get("ref_index") or 0)
                except (TypeError, ValueError):
                    continue
                if ref <= 0 or (provider, ref) in seen:
                    continue
                seen.add((provider, ref))
                as_of_raw = c.get("as_of")
                as_of_d: date | None = None
                if isinstance(as_of_raw, str) and as_of_raw:
                    try:
                        as_of_d = date.fromisoformat(as_of_raw[:10])
                    except ValueError:
                        as_of_d = None
                citations.append(
                    Citation(
                        tag=f"[{provider}-{ref}]",
                        provider=provider,
                        ref_index=ref,
                        source_url=(str(c.get("source_url")) if c.get("source_url") else None),
                        snippet=(str(c.get("snippet")) if c.get("snippet") else None),
                        as_of=as_of_d,
                    )
                )
        return {"content": content, "citations": citations}


# ============================================================
# 4) Judge Agent（收敛检查：缺节 / 引用 / 置信度）
# ============================================================
class JudgeAgent(BaseAgent):
    agent_name = "judge"
    template_path = "judge.jinja2"

    def build_context(  # type: ignore[override]
        self,
        ticker: str,
        draft_sections: list[dict[str, Any]],
        section_ids_expected: list[str],
        stale_warnings: list[str] | None = None,
        style: str = "analyst",
    ) -> dict[str, Any]:
        filled_ids = [str(s.get("section_id")) for s in draft_sections if isinstance(s, dict) and s.get("section_id")]
        missing = [sid for sid in section_ids_expected if sid not in filled_ids]
        preview: list[dict[str, Any]] = []
        for s in draft_sections:
            if not isinstance(s, dict):
                continue
            text = str(s.get("plain_text") or s.get("content") or "")
            n_cit = len(s.get("citations") or [])
            preview.append(
                {
                    "section_id": str(s.get("section_id") or ""),
                    "chars": len(text),
                    "citations": n_cit,
                    "preview": text[:200],
                }
            )
        return {
            "ticker": ticker,
            "style": style,
            "section_ids_expected": section_ids_expected,
            "filled_ids": filled_ids,
            "missing_ids": missing,
            "draft_preview": preview,
            "stale_warnings": stale_warnings or [],
            "expected_output": (
                "JSON { "
                "converged: bool, "
                "missing_sections: Array<str>, "
                "weak_sections: Array<{section_id, reason}>, "
                "rating: 'bullish'|'neutral'|'bearish', "
                "key_drivers: Array<str>, key_risks: Array<str>, "
                "confidence: 'high'|'medium'|'low', confidence_notes: Array<str> }"
            ),
        }

    def parse_response(self, raw: str) -> dict[str, Any]:  # type: ignore[override]
        data = _safe_json_loads(raw)
        if not isinstance(data, dict):
            raise ValueError(f"JudgeAgent 需要 JSON object，实际 {type(data)!r}")
        converged = bool(data.get("converged", False))
        missing = [str(x) for x in (data.get("missing_sections") or []) if str(x).strip()]
        weak_raw = data.get("weak_sections") or []
        weak: list[dict[str, str]] = []
        if isinstance(weak_raw, list):
            for w in weak_raw:
                if not isinstance(w, dict):
                    continue
                sid = str(w.get("section_id") or "").strip()
                if not sid:
                    continue
                weak.append({"section_id": sid, "reason": str(w.get("reason") or "弱证据")})
        rating = str(data.get("rating") or "neutral").lower()
        if rating not in {"bullish", "neutral", "bearish"}:
            rating = "neutral"
        key_drivers = [str(x) for x in (data.get("key_drivers") or []) if str(x).strip()]
        key_risks = [str(x) for x in (data.get("key_risks") or []) if str(x).strip()]
        confidence = str(data.get("confidence") or "low").lower()
        if confidence not in {"high", "medium", "low"}:
            confidence = "low"
        notes = [str(x) for x in (data.get("confidence_notes") or []) if str(x).strip()]
        return {
            "converged": converged and not missing,
            "missing_sections": missing,
            "weak_sections": weak,
            "rating": rating,
            "key_drivers": key_drivers,
            "key_risks": key_risks,
            "confidence": confidence,
            "confidence_notes": notes,
        }


# ============================================================
# 5) Report Agent（高阶润色 + 结论段 + Confidence 汇总）
# ============================================================
class ReportAgent(BaseAgent):
    agent_name = "report"
    template_path = "report.jinja2"

    def build_context(  # type: ignore[override]
        self,
        ticker: str,
        filled_sections: list[CitedParagraph],
        peer_matrix: PeerComparisonMatrix | None,
        analyst_tags: list[str],
        style: str,
    ) -> dict[str, Any]:
        ser_sections = [s.model_dump(mode="json") for s in filled_sections]
        return {
            "ticker": ticker,
            "style": style,
            "filled_sections": ser_sections,
            "peer_matrix": peer_matrix.model_dump(mode="json") if peer_matrix else None,
            "analyst_tags": analyst_tags or [],
            "expected_output": (
                "JSON { "
                "sections_overview: str, "
                "thesis_conclusion: str, "
                "competitive_edges: str, "
                "catalysts: str, "
                "risks: str }"
                "（section 正文保留 [PROV-N] 引用标记）"
            ),
        }

    def parse_response(self, raw: str) -> dict[str, Any]:  # type: ignore[override]
        data = _safe_json_loads(raw)
        if not isinstance(data, dict):
            raise ValueError(f"ReportAgent 需要 JSON object，实际 {type(data)!r}")
        required = ("sections_overview", "thesis_conclusion", "competitive_edges", "catalysts", "risks")
        for k in required:
            if len(str(data.get(k) or "")) < 20:
                raise ValueError(f"ReportAgent.{k} 内容过短")
        return {k: str(data[k]).strip() for k in required}


# ============================================================
# Bocha 搜索封装（Phase 3~4 调用，失败返回 [] degrade）
# ============================================================
class BochaSearch:
    def __init__(self, base_url: str = BOCHA_BASE_URL, api_key: str = BOCHA_API_KEY, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = (api_key or "").strip()
        self.timeout = timeout

    def is_available(self) -> bool:
        if not self.api_key:
            return False
        low = self.api_key.lower()
        if low.startswith("your_") or "sk-xxx" in low or "your key" in low:
            return False
        return True

    def search(self, query: str, count: int = 5) -> list[dict[str, Any]]:
        if not self.is_available():
            return []
        try:
            headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
            payload = {"query": query, "count": int(count)}
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(f"{self.base_url}/web-search", headers=headers, json=payload)
                if resp.status_code != 200:
                    logger.warning("[Bocha] HTTP %s: %s", resp.status_code, resp.text[:200])
                    return []
                data = resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[Bocha] 请求异常：%s", exc)
            return []
        out: list[dict[str, Any]] = []
        items = None
        if isinstance(data, dict):
            if isinstance(data.get("data"), dict):
                items = data["data"].get("results") or data["data"].get("items") or data["data"].get("webPages")
            elif isinstance(data.get("data"), list):
                items = data["data"]
            elif isinstance(data.get("results"), list):
                items = data["results"]
        if not isinstance(items, list):
            return out
        for idx, it in enumerate(items, start=1):
            if not isinstance(it, dict):
                continue
            out.append(
                {
                    "ref_index": idx,
                    "provider": "BOCHA",
                    "title": str(it.get("title") or ""),
                    "snippet": str(it.get("snippet") or it.get("summary") or it.get("description") or ""),
                    "url": str(it.get("url") or it.get("link") or ""),
                    "date": str(it.get("date") or it.get("datePublishedCrawled") or ""),
                }
            )
        return out


# ============================================================
# Engine：8 阶段编排
# ============================================================
class ETFResearchEngine:
    """Spec §5.4 8 阶段流水线。"""

    def __init__(
        self,
        storage: Storage | None = None,
        providers: list[DataProvider] | None = None,
        cache: ResultCache | None = None,
        exporter: ReportExporter | None = None,
        on_progress: ProgressCb | None = None,
    ) -> None:
        self.storage = storage or Storage()
        self.cache = cache or ResultCache(CACHE_DIR)
        self.exporter = exporter or ReportExporter()
        self.on_progress: ProgressCb = on_progress or (lambda *a, **kw: None)
        # Provider 初始化（免费数据源先实例化，付费按需 Key 启用）
        default_providers: list[DataProvider] = providers or [
            PolygonProvider(),
            AlphaVantageProvider(),
            YahooFinanceProvider(),
            ETFDBProvider(),
        ]
        self.registry = ProviderRegistry(default_providers)
        self.bocha = BochaSearch()
        self.schema = ETFSchema()
        self.peer_resolver = PeerResolver()
        self.citation_parser = CitationParser()
        self.freshness = FreshnessValidator()

    # ---------- 工具 ----------
    def _progress(self, type_: str, msg: str, research_id: str = "") -> StepLog:
        self.on_progress(type_, msg)
        return StepLog(type=type_, message=msg, extra={"research_id": research_id})

    def _registry_cached(
        self,
        method: str,
        ticker: str,
        *args: Any,
        **kwargs: Any,
    ) -> tuple[Any, str, bool]:
        today = date.today()
        extra_args = {"args": [str(a) for a in args], "kwargs": {k: str(v) for k, v in kwargs.items()}}
        hit, cached = self.cache.get(ticker, method, today=today, extra_args=extra_args)
        if hit:
            # cached=None 表示空值占位命中，直接 degrade 让上层 Bocha
            if cached is None:
                return {}, "CACHE_EMPTY", True
            return cached, f"CACHE:{cached.get('__provider__','?')}", False
        result, provider, degraded = self.registry.fetch_first_available(method, ticker, *args, **kwargs)
        if degraded:
            self.cache.set(ticker, method, None, today=today, extra_args=extra_args)
        else:
            to_store: Any = result
            if hasattr(result, "model_dump"):
                to_store = result.model_dump(mode="json")
                to_store["__provider__"] = provider
            self.cache.set(ticker, method, to_store, today=today, extra_args=extra_args)
        return result, provider, degraded

    # ---------- Bocha 批搜 ----------
    def _bocha_batch(self, queries: list[str], count: int = 3) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        seen_url: set[str] = set()
        for q in queries or []:
            for item in self.bocha.search(q, count=count):
                url = str(item.get("url") or "").strip()
                if url and url in seen_url:
                    continue
                if url:
                    seen_url.add(url)
                out.append(item)
        return out

    # ---------- 主流水线 ----------
    def run(
        self,
        research_id: str,
        ticker: str,
        style: Literal["analyst", "institutional", "explainer"] = "analyst",
        peers: int = 5,
        extra_peer_tickers: list[str] | None = None,
    ) -> ResearchRecord:
        ticker = ticker.strip().upper()
        if not ticker:
            raise ValueError("ticker 不能为空")
        record = self.storage.get(research_id)
        if record is None:
            record = self.storage.new_record(ticker=ticker, style=style, peers=peers)
            record.research_id = research_id
        record.status = "running"
        record.updated_at = datetime.utcnow()
        record.process.steps.append(self._progress("phase0", "解析参数 & 构造 Registry", research_id))
        self.storage.save(record)

        # ---------- Phase 1：Provider 批量拉取 + Cache ----------
        try:
            record.process.steps.append(self._progress("phase1", f"Provider 批量拉取 ticker={ticker}", research_id))
            profile_raw, p_prov, p_deg = self._registry_cached("get_profile", ticker)
            history_raw, h_prov, h_deg = self._registry_cached("get_history", ticker, "1y")
            risk_raw, r_prov, r_deg = self._registry_cached("get_risk_metrics", ticker, "SPY")
            holdings_raw, ho_prov, ho_deg = self._registry_cached("get_holdings", ticker)
            flow_raw, f_prov, f_deg = self._registry_cached("get_fund_flow", ticker)

            # 对 Pydantic / dict 兼容
            def _to_model(cls: Any, raw: Any, fallback_ticker: str, fallback_provider: str) -> Any:
                if isinstance(raw, cls):
                    return raw
                if isinstance(raw, dict):
                    try:
                        return cls.model_validate(raw)
                    except Exception:
                        pass
                return cls(ticker=fallback_ticker, source_tag=f"{fallback_provider}:EMPTY")

            profile: ETFProfile = _to_model(ETFProfile, profile_raw, ticker, p_prov)
            history: QuoteHistory = _to_model(QuoteHistory, history_raw, ticker, h_prov)
            risk: RiskMetrics = _to_model(RiskMetrics, risk_raw, ticker, r_prov)
            holdings: Holdings = _to_model(Holdings, holdings_raw, ticker, ho_prov)
            flow: FundFlow = _to_model(FundFlow, flow_raw, ticker, f_prov)

            # Peer 解析 + 同行小档案批量
            peer_res: PeerResolution = self.peer_resolver.resolve_peers(
                category=profile.category,
                focus_index=profile.focus_index,
                aum_usd_b=float((profile.aum_usd_m or 0.0) / 1000.0),
                count=max(5, min(8, int(peers))),  # type: ignore[arg-type]
                extra_peer_tickers=extra_peer_tickers,
            )
            record.process.steps.append(
                self._progress(
                    "phase1",
                    f"解析到 peers={peer_res.peer_tickers!r}",
                    research_id,
                )
            )
            peer_rows: list[PeerInfoRow] = []
            for pt in peer_res.peer_tickers:
                try:
                    pp_raw, _, pp_deg = self._registry_cached("get_profile", pt)
                    rp_raw, _, _ = self._registry_cached("get_risk_metrics", pt, "SPY")
                    if isinstance(pp_raw, ETFProfile):
                        pp = pp_raw
                    elif isinstance(pp_raw, dict):
                        try:
                            pp = ETFProfile.model_validate(pp_raw)
                        except Exception:
                            pp = ETFProfile(ticker=pt, source_tag="EMPTY")
                    else:
                        pp = ETFProfile(ticker=pt, source_tag=str(pp_raw)[:32] or "EMPTY")
                    if isinstance(rp_raw, RiskMetrics):
                        rp = rp_raw
                    else:
                        rp = RiskMetrics(ticker=pt)
                    peer_rows.append(
                        PeerInfoRow(
                            ticker=pt,
                            name=pp.name or pt,
                            aum_usd_m=pp.aum_usd_m,
                            expense_ratio=pp.expense_ratio,
                            ytd_return_pct=getattr(pp, "ytd_return_pct", None),
                            one_y_return_pct=getattr(pp, "one_y_return_pct", None),
                            three_y_annual_pct=getattr(pp, "three_y_annual_pct", None),
                            sharpe_3y=rp.sharpe_3y,
                            max_drawdown_pct=rp.max_drawdown_pct,
                            beta=rp.beta,
                            dividend_yield_pct=pp.dividend_yield_pct,
                            as_of=pp.as_of or rp.as_of or date.today(),
                            source_tag=pp.source_tag or rp.source_tag or "PEER_EMPTY",
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[Engine] peer=%s fetch fail: %s", pt, exc)
                    continue
            peer_matrix = self.peer_resolver.assemble_matrix(peer_rows)
            self.exporter._stash_profile = profile  # type: ignore[attr-defined]
            self.exporter._stash_history = history  # type: ignore[attr-defined]
            self.exporter._stash_holdings = holdings  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            record.status = "failed"
            record.error = f"Phase1 Provider 失败：{exc!s}"
            self.storage.save(record)
            return record

        # ---------- Phase 2：Freshness 时效 + 缺节识别 ----------
        record.process.steps.append(self._progress("phase2", "Freshness 校验 + 缺节识别", research_id))
        stale_fields = []
        stale_fields.extend(self.freshness.check(profile, path_prefix="profile"))
        stale_fields.extend(self.freshness.check(history, path_prefix="performance"))
        stale_fields.extend(self.freshness.check(risk, path_prefix="risk_metrics"))
        stale_fields.extend(self.freshness.check(holdings, path_prefix="holdings"))
        stale_fields.extend(self.freshness.check(flow, path_prefix="fundflow"))
        stale_msgs = [
            f"{s.field_path} as_of={s.as_of} age={s.age_days}d > {s.policy_max_days}d" for s in stale_fields
        ]
        missing_sections = self.schema.section_ids.copy()  # 默认 10 节都缺
        record.updated_at = datetime.utcnow()
        self.storage.save(record)

        # ---------- Phase 3：Planning 生成 Section × Query 矩阵 ----------
        record.process.steps.append(self._progress("phase3", "PlanningAgent 生成调研计划", research_id))
        def _on_progress_agent(type_: str, msg: str) -> None:
            record.process.steps.append(StepLog(type=f"agent:{type_}", message=msg))
            self.storage.save(record)

        planning_agent = PlanningAgent(on_progress=_on_progress_agent)
        try:
            plan = planning_agent.generate(
                ticker=ticker,
                missing_sections=missing_sections,
                peer_matrix_summary=(
                    f"main_ticker={peer_matrix.main_ticker} peers_count={len(peer_matrix.rows)}"
                ),
                style=style,
                profile=profile,
                stale_warnings=stale_msgs,
                max_rounds=2,
            )
        except Exception as exc:  # noqa: BLE001
            plan = [
                {
                    "section_id": sid,
                    "section_title": self.schema.section_id_to_title(sid),
                    "keywords": [ticker, sid],
                    "bocha_queries": [f"{ticker} {sid} 2026"],
                }
                for sid in missing_sections
            ]
            record.process.steps.append(
                StepLog(type="degrade", message=f"PlanningAgent 失败 → 降级为默认计划：{exc!s}")
            )
        record.process.plan = [f"{p['section_id']}: {p['section_title']}" for p in plan]
        self.storage.save(record)

        # ---------- Phase 4~N：逐 Section 关键词扩展 + Bocha + Summary ----------
        record.process.steps.append(self._progress("phase4", "逐 Section 关键词 + Bocha + Summary 写作", research_id))
        filled: dict[str, CitedParagraph] = {}
        sections_queue: list[dict[str, Any]] = list(plan)
        iterations = 0
        max_iter = MAX_ROUNDS
        while iterations < max_iter and sections_queue:
            iterations += 1
            record.process.iterations = iterations
            next_queue: list[dict[str, Any]] = []
            for slot in sections_queue:
                sid = str(slot.get("section_id") or "")
                if not sid or sid in filled:
                    continue
                title = str(slot.get("section_title") or self.schema.section_id_to_title(sid))
                kw_agent = KeywordAgent(on_progress=_on_progress_agent)
                try:
                    keywords = kw_agent.generate(
                        ticker=ticker,
                        section_id=sid,
                        section_title=title,
                        style=style,
                        existing_keywords=list(slot.get("keywords") or []),
                        max_rounds=2,
                    )
                except Exception as exc:  # noqa: BLE001
                    keywords = [ticker, sid, title]
                    record.process.steps.append(
                        StepLog(type="degrade", message=f"KeywordAgent {sid} 失败：{exc!s}")
                    )
                record.process.search_queries.extend([f"{sid}: {k}" for k in keywords])
                bocha_queries = list(slot.get("bocha_queries") or []) or [
                    f"{ticker} {k} 2026 site:etfdb.com OR site:finance.yahoo.com" for k in keywords[:3]
                ]
                bocha_results = self._bocha_batch(bocha_queries, count=3)
                record.process.reviewed_urls.extend(
                    [str(x.get("url") or "") for x in bocha_results if x.get("url")]
                )
                existing = filled[sid].plain_text if sid in filled else ""
                sum_agent = SummaryAgent(on_progress=_on_progress_agent)
                try:
                    parsed = sum_agent.generate(
                        ticker=ticker,
                        section_id=sid,
                        section_title=title,
                        bocha_results=bocha_results,
                        existing_content=existing,
                        style=style,
                        max_rounds=2,
                    )
                    content = parsed["content"]
                    citations: list[Citation] = list(parsed["citations"])
                    # 补行内正则扫出但漏标的引用
                    inline = self.citation_parser.scan_citations(content)
                    existing_tags = {(c.provider, c.ref_index) for c in citations}
                    for c in inline:
                        if (c.provider, c.ref_index) not in existing_tags:
                            citations.append(c)
                    cp = CitedParagraph(
                        section_id=sid,
                        plain_text=content,
                        citations=citations,
                    )
                    filled[sid] = cp
                except Exception as exc:  # noqa: BLE001
                    record.process.steps.append(
                        StepLog(type="degrade", message=f"SummaryAgent {sid} round={iterations} 失败：{exc!s}")
                    )
                    # 下轮再试
                    next_queue.append(slot)
            sections_queue = next_queue
            record.updated_at = datetime.utcnow()
            self.storage.save(record)

        # 所有缺节兜底：用 Provider dict 信息生成最小段落
        for sid in self.schema.section_ids:
            if sid in filled:
                continue
            summary = (
                f"【{self.schema.section_id_to_title(sid)}】本段因 LLM 写作失败，以结构化数据兜底："
                f"ticker={ticker}; profile.category={profile.category}; "
                f"aum_usd_m={profile.aum_usd_m}; expense_ratio={profile.expense_ratio}。"
                "如需完整内容请重试或增加 MAX_ROUNDS。"
            )
            filled[sid] = CitedParagraph(
                section_id=sid,
                plain_text=summary,
                citations=[],
            )

        # ---------- Phase 5：Judge 收敛 ----------
        record.process.steps.append(self._progress("phase5", "JudgeAgent 收敛检查", research_id))
        judge = JudgeAgent(on_progress=_on_progress_agent)
        draft_ser = [
            {
                "section_id": cp.section_id,
                "plain_text": cp.plain_text,
                "citations": [c.model_dump(mode="json") for c in cp.citations],
            }
            for cp in filled.values()
        ]
        try:
            judge_out = judge.generate(
                ticker=ticker,
                draft_sections=draft_ser,
                section_ids_expected=self.schema.section_ids,
                stale_warnings=stale_msgs,
                max_rounds=2,
            )
        except Exception as exc:  # noqa: BLE001
            judge_out = {
                "converged": True,
                "missing_sections": [],
                "weak_sections": [],
                "rating": "neutral",
                "key_drivers": [f"ticker={ticker} 数据新鲜度未知"],
                "key_risks": ["JudgeAgent 收敛失败，降级 neutral"],
                "confidence": "low",
                "confidence_notes": [f"JudgeAgent 失败：{exc!s}"],
            }
            record.process.steps.append(
                StepLog(type="degrade", message=f"JudgeAgent 失败：{exc!s}")
            )

        analyst_rating = AnalystRating(
            rating=judge_out["rating"],  # type: ignore[arg-type]
            key_drivers=list(judge_out["key_drivers"]),
            key_risks=list(judge_out["key_risks"]),
        )
        confidence = Confidence(
            overall=judge_out["confidence"],  # type: ignore[arg-type]
            info_cutoff=max(
                [x for x in [profile.as_of, history.as_of, risk.as_of, holdings.as_of, flow.as_of, peer_matrix.as_of] if x],
                default=date.today(),
            ),
            notes=list(judge_out["confidence_notes"]),
        )
        record.updated_at = datetime.utcnow()
        self.storage.save(record)

        # ---------- Phase 6：ReportAgent 高阶润色（补齐竞争优势/催化/风险/结论） ----------
        record.process.steps.append(self._progress("phase6", "ReportAgent 高阶润色 + 结论", research_id))
        report_agent = ReportAgent(on_progress=_on_progress_agent)
        ordered_sections: list[CitedParagraph] = []
        for sid in self.schema.section_ids:
            if sid in filled:
                ordered_sections.append(filled[sid])
        analyst_tags = [judge_out["rating"]] + list(judge_out["key_drivers"][:3])
        try:
            report_polish = report_agent.generate(
                ticker=ticker,
                filled_sections=ordered_sections,
                peer_matrix=peer_matrix,
                analyst_tags=analyst_tags,
                style=style,
                max_rounds=2,
            )
            # 覆写 competitive_edges / catalysts / risks / thesis_conclusion，加 conclusion 前免责
            overrides = {
                "competitive_edges": report_polish["competitive_edges"],
                "catalysts": report_polish["catalysts"],
                "risks": report_polish["risks"],
                "thesis_conclusion": report_polish["thesis_conclusion"],
            }
            for sid, text in overrides.items():
                if sid in filled:
                    old = filled[sid]
                    filled[sid] = CitedParagraph(
                        section_id=sid,
                        plain_text=text,
                        citations=old.citations,
                    )
                else:
                    filled[sid] = CitedParagraph(section_id=sid, plain_text=text, citations=[])
        except Exception as exc:  # noqa: BLE001
            record.process.steps.append(
                StepLog(type="degrade", message=f"ReportAgent 失败：{exc!s}")
            )

        # ---------- Phase 7：Confidence + 4 格式 7 文件导出（B1 三位置免责 + B2 CSV as_of/source_tag） ----------
        record.process.steps.append(self._progress("phase7", "构造 ReportContent + 4 格式 7 文件真写出", research_id))
        ordered_sections = []
        for sid in self.schema.section_ids:
            if sid in filled:
                ordered_sections.append(filled[sid])
        report = ReportContent(
            ticker=ticker,
            report_date=date.today(),
            style=style,  # type: ignore[arg-type]
            sections=ordered_sections,
            peer_matrix=peer_matrix,
            analyst_rating=analyst_rating,
            stale_warnings=stale_msgs,
            confidence=confidence,
            process=record.process,
            disclaimer=Disclaimer(),
        )
        record.report = report

        paths = self.storage.export_paths(research_id)
        try:
            self.exporter.export_json(report, paths["json"])
            self.exporter.export_markdown(report, paths["md"])
            self.exporter.export_html(report, paths["html"])
            self.exporter.export_profile_csv(report, paths["profile_csv"])
            self.exporter.export_history_csv(report, paths["history_csv"])
            self.exporter.export_holdings_csv(report, paths["holdings_csv"])
            if peer_matrix is not None:
                self.exporter.export_peer_matrix_csv(peer_matrix, paths["peer_csv"])
        except Exception as exc:  # noqa: BLE001
            record.process.steps.append(
                StepLog(type="degrade", message=f"批次3 Exporter 7 文件写出失败：{exc!s}")
            )
        # 始终注册 7 路径到 record.sources（便于 main/list 查到）
        record.sources.append({"step": "phase7_export_paths", "paths": {k: str(v) for k, v in paths.items()}})
        record.status = "completed"
        record.updated_at = datetime.utcnow()
        self.storage.save(record)
        return record


# ============================================================
# Service：CLI + API 双模式 Facade
# ============================================================
class ETFService:
    """CLI + FastAPI 共用 Service：Engine run 异步化、get/list 查 Storage。"""

    def __init__(
        self,
        storage: Storage | None = None,
        engine: ETFResearchEngine | None = None,
    ) -> None:
        self.storage = storage or Storage()
        self.engine = engine or ETFResearchEngine(storage=self.storage)
        self._records: dict[str, ResearchRecord] = {}

    def _new_id(self, ticker: str) -> str:
        ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        return f"R_{ts}_{ticker.upper()}_{uuid.uuid4().hex[:6]}"

    def start_research(
        self,
        ticker: str,
        style: Literal["analyst", "institutional", "explainer"] = "analyst",
        peers: int = 5,
        extra_peer_tickers: list[str] | None = None,
    ) -> ResearchRecord:
        research_id = self._new_id(ticker)
        seed = self.storage.new_record(ticker=ticker, style=style, peers=peers)
        seed.research_id = research_id
        self.storage.save(seed)
        self._records[research_id] = seed
        # 同步执行（CLI），API 层可以用 BackgroundTasks 包一层；Engine 自己会 save 多次
        done = self.engine.run(
            research_id=research_id,
            ticker=ticker,
            style=style,
            peers=peers,
            extra_peer_tickers=extra_peer_tickers,
        )
        self._records[research_id] = done
        return done

    def get_record(self, research_id: str) -> ResearchRecord | None:
        return self.storage.get(research_id) or self._records.get(research_id)

    def list_records(self, limit: int = 50) -> list[ResearchRecord]:
        return self.storage.list(limit=limit)
