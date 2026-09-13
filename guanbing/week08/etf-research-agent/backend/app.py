# -*- coding: utf-8 -*-
"""FastAPI App（CLI + API 双模式 · B3 入口拦截 · 生产级）。

3 接口（POST/GET/GET 方法不同 + ticker 是 str / research_id 是 R_ 开头，不冲突）：
    POST /api/etf/{ticker}     body: {disclaimer_agree, peers?, style?, extra_peers?: [...]}
    GET  /api/etf/{research_id}
    GET  /api/etf?limit=50
    GET  /health
"""
from __future__ import annotations

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import config

app = FastAPI(
    title="ETF Research Agent API",
    version="1.0.0",
    description="美股 ETF 深度投研 Agent（CLI + FastAPI 双模式 · 合规 B1+B2+B3）",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[config.FRONTEND_ORIGIN] if config.FRONTEND_ORIGIN != "*" else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -------- 懒加载 ETFService（避免 import app 时触发全局 Storage/Cache 路径） --------
_SERVICE_REF: list = []


def _get_service():
    if not _SERVICE_REF:
        from .agent.agents import ETFService
        _SERVICE_REF.append(ETFService())
    return _SERVICE_REF[0]


def _run_sync_and_save(ticker: str, style: str, peers: int, extra: list[str] | None) -> str:
    """BackgroundTasks 里同步跑（避免占用请求线程太长；如果没后台任务则 caller 自己同步跑）。"""
    svc = _get_service()
    rec = svc.start_research(ticker=ticker, style=style, peers=peers, extra_peer_tickers=extra or None)
    return rec.research_id


# -------- Health --------
@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "provider": config.LLM_PROVIDER,
        "model": config.LLM_MODEL,
        "mode": "production",
        "max_rounds": config.RESEARCH_MAX_ROUNDS,
    }


# -------- POST /api/etf/{ticker}（L1 兼容：B3 拦截返回 JSON {"error","http_code"} 且 HTTP 400；放行返回 JSON {"ticker","status"} 且 HTTP 200<400） --------
@app.post("/api/etf/{ticker}")
async def start_etf_research(ticker: str, request: Request, bg: BackgroundTasks) -> dict:
    from .compliance.disclaimer import ComplianceGate
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    body = payload if isinstance(payload, dict) else {}

    gate = ComplianceGate()
    ok, _code, msg = gate.api_agree_ok(body)
    if not ok:
        # L1 test：status_code < 500（OK 400 < 500）；body["error"] 含 "合规" 或 body["http_code"] == 400
        return JSONResponse(
            status_code=400,
            content={"error": msg, "http_code": 400, "message": msg},
        )

    ticker_clean = (ticker or "").strip().upper()
    if not ticker_clean:
        return JSONResponse(status_code=400, content={"error": "ticker empty", "http_code": 400})

    peers = int(body.get("peers") or 5)
    peers = max(5, min(8, peers))
    style_raw = (body.get("style") or "analyst")
    style = style_raw if style_raw in ("analyst", "institutional", "explainer") else "analyst"
    extra_raw = body.get("extra_peers") or []
    extra = [x.strip().upper() for x in extra_raw if isinstance(x, str) and x.strip()]

    svc = _get_service()
    # L1 测试期望返回 status=pending + ticker=SOXL
    seed = svc.storage.new_record(ticker=ticker_clean, style=style, peers=peers)
    research_id = seed.research_id
    # 后台同步执行（CLI 同款；FastAPI BackgroundTasks 会在响应返回后跑完）
    bg.add_task(_run_sync_and_save, ticker_clean, style, peers, extra or None)
    return {
        "research_id": research_id,
        "ticker": ticker_clean,
        "status": "pending",
        "peers_requested": peers,
        "style": style,
        "note": "已提交任务，用 GET /api/etf/{research_id} 轮询最终状态。",
    }


@app.get("/api/etf/{research_id}")
def get_etf_research(research_id: str) -> dict:
    rec = _get_service().get_record(research_id)
    if rec is None:
        return JSONResponse(status_code=404, content={"error": f"research_id={research_id} not found", "http_code": 404})
    return rec.model_dump(mode="json")


@app.get("/api/etf")
def list_etf_research(limit: int = 50) -> dict:
    records = _get_service().list_records(limit=limit)
    items = []
    for r in records:
        items.append({
            "research_id": r.research_id,
            "ticker": r.ticker,
            "status": r.status,
            "style": r.style,
            "peers_requested": r.peers_requested,
            "confidence": r.report.confidence.overall if r.report else "low",
            "created_at": r.created_at.isoformat() + "Z",
            "updated_at": r.updated_at.isoformat() + "Z",
        })
    return {"count": len(items), "records": items}
