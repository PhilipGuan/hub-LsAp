#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI 入口 · 生产级（Spec §6 M6 CLI 参数签名 + 真实 ETFService）。

用法：
    python main.py research SOXL --peers 5 --style analyst --disclaimer-agree
    python main.py research SOXL --style institutional --extra-peers XLE SMH --disclaimer-agree --json
    python main.py list --limit 50 [--json]
    python main.py get R_xxxxxx [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from backend.agent.agents import ETFService
from backend.compliance.disclaimer import ComplianceGate


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="etf-research-agent",
        description="美股 ETF 深度投研 Agent（CLI + FastAPI 双模式）",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_research = sub.add_parser("research", help="启动一只 ETF 的投研任务（同步执行，直到完成）")
    p_research.add_argument("ticker", help="ETF 代码，例如 SOXL")
    p_research.add_argument("--peers", type=int, default=5, choices=[5, 6, 7, 8],
                            help="强制同类对比 ETF 数量（生产级 B 方案）")
    p_research.add_argument("--style", type=str, default="analyst",
                            choices=["analyst", "institutional", "explainer"],
                            help="Prompt 风格切换")
    p_research.add_argument("--extra-peers", nargs="*", default=None,
                            help="手工追加 1~3 只对标代码（可选）")
    p_research.add_argument("--disclaimer-agree", action="store_true",
                            help="合规 B3：必须显式带此参数才会执行")
    p_research.add_argument("--json", action="store_true",
                            help="以 JSON 输出 CLI 结果")

    p_list = sub.add_parser("list", help="查看历史研究列表（读 Storage index.jsonl）")
    p_list.add_argument("--limit", type=int, default=50)
    p_list.add_argument("--json", action="store_true")

    p_get = sub.add_parser("get", help="查看单个研究详情（record.json + 所有导出文件路径）")
    p_get.add_argument("research_id")
    p_get.add_argument("--json", action="store_true")

    return parser


def _record_to_dict(rec) -> dict:
    d = rec.model_dump(mode="json")
    return d


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.cmd == "research":
        ok, code, msg = ComplianceGate().cli_agree_ok(args.disclaimer_agree)
        if not ok:
            print(f"[合规 B3 ❌] {msg}", file=sys.stderr)
            print(f"    ➡️  正确用法：python main.py research {args.ticker} --disclaimer-agree", file=sys.stderr)
            return code

        service = ETFService()
        ticker = args.ticker.strip().upper()
        extra = [p.strip().upper() for p in (args.extra_peers or []) if p and p.strip()]
        t0 = datetime.utcnow()
        if not args.json:
            print(f"[{t0.isoformat()[:19]}Z] 启动研究 ticker={ticker} style={args.style} peers={args.peers} extra={extra or '[]'}")
        rec = service.start_research(
            ticker=ticker,
            style=args.style,  # type: ignore[arg-type]
            peers=args.peers,
            extra_peer_tickers=extra or None,
        )
        t1 = datetime.utcnow()
        dur = (t1 - t0).total_seconds()
        # 找所有 exports 文件，统计大小
        paths_map = {}
        try:
            for entry in rec.sources or []:
                if isinstance(entry, dict) and entry.get("step") == "phase7_export_paths":
                    paths_map = entry.get("paths") or {}
        except Exception:  # noqa: BLE001
            paths_map = {}
        file_sizes = {}
        for k, v in paths_map.items():
            p = Path(v)
            file_sizes[k] = p.stat().st_size if p.exists() else 0
        out = {
            "research_id": rec.research_id,
            "ticker": rec.ticker,
            "status": rec.status,
            "peers_requested": rec.peers_requested,
            "style": rec.style,
            "confidence": rec.report.confidence.overall if rec.report else "low",
            "stale_warnings": rec.report.stale_warnings if rec.report else [],
            "duration_seconds": round(dur, 2),
            "export_files": file_sizes,
            "error": rec.error,
        }
        if args.json:
            print(json.dumps(out, ensure_ascii=False, indent=2))
        else:
            print(f"[{t1.isoformat()[:19]}Z] 完成 status={rec.status} id={rec.research_id} confidence={out['confidence']} 用时={dur:.2f}s")
            for k, size in file_sizes.items():
                exists_marker = "✅" if size > 0 else "❌0B"
                print(f"  · {exists_marker} {k}: {paths_map.get(k,'')} ({size}B)")
        return 0 if rec.status != "failed" else 3

    service = ETFService()

    if args.cmd == "list":
        records = service.list_records(limit=args.limit)
        summary = []
        for r in records:
            summary.append({
                "research_id": r.research_id,
                "ticker": r.ticker,
                "status": r.status,
                "style": r.style,
                "peers_requested": r.peers_requested,
                "created_at": r.created_at.isoformat() + "Z" if hasattr(r.created_at, "isoformat") else str(r.created_at),
                "updated_at": r.updated_at.isoformat() + "Z" if hasattr(r.updated_at, "isoformat") else str(r.updated_at),
                "confidence": r.report.confidence.overall if r.report else "low",
                "error": r.error,
            })
        out = {"count": len(summary), "records": summary}
        if args.json:
            print(json.dumps(out, ensure_ascii=False, indent=2))
        else:
            for s in summary:
                print(f"[{s['status']:<9}] {s['research_id']}  {s['ticker']:<6}  style={s['style']}  peers={s['peers_requested']}  conf={s['confidence']:<6}  updated={s['updated_at']}")
            print(f"合计 {len(summary)} 条（limit={args.limit}）")
        return 0

    if args.cmd == "get":
        rec = service.get_record(args.research_id)
        if rec is None:
            err = {"research_id": args.research_id, "error": "not found"}
            if args.json:
                print(json.dumps(err, ensure_ascii=False, indent=2))
            else:
                print(f"[NOT FOUND] research_id={args.research_id} 不存在")
            return 1
        d = _record_to_dict(rec)
        # 简化大字段：sections 只截 chars 汇总 + draft/sources 缩略
        if d.get("report", {}).get("sections"):
            seccount = len(d["report"]["sections"])
            total_chars = sum(len(s.get("plain_text") or "") for s in d["report"]["sections"])
            d["report"]["sections"] = {"count": seccount, "total_chars": total_chars, "note": "太长，省略原文"}
        if args.json:
            print(json.dumps(d, ensure_ascii=False, indent=2))
        else:
            print(f"[{d['status']}] {d['research_id']}  ticker={d['ticker']}  style={d['style']}  peers_req={d['peers_requested']}")
            print(f"  created={d['created_at']}  updated={d['updated_at']}")
            if d.get("report"):
                r = d["report"]
                print(f"  confidence={r['confidence']['overall']}  rating={r.get('analyst_rating',{}) and r.get('analyst_rating',{}).get('rating','?')}")
                print(f"  sections={r['sections']}")
                if r.get("stale_warnings"):
                    print(f"  stale={r['stale_warnings']}")
                for entry in d.get("sources") or []:
                    if isinstance(entry, dict) and entry.get("step") == "phase7_export_paths":
                        print("  export_paths:")
                        for k, v in (entry.get("paths") or {}).items():
                            print(f"    · {k}: {v}")
            if d.get("error"):
                print(f"  error={d['error']}")
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
