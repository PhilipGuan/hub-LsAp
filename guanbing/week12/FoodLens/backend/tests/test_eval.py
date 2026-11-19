"""评估集回归：菜名命中率 + 热量相对误差（demo provider，全程离线）。

门槛（与设计文档一致）：菜名命中率 ≥ 90%，热量相对误差 ≤ 15%。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.config import Settings
from app.orchestrator import analyze

CASES_PATH = Path(__file__).resolve().parent.parent / "eval" / "cases.json"

HIT_RATE_MIN = 0.90
KCAL_TOLERANCE = 0.15


def _load_cases() -> list[dict]:
    return json.loads(CASES_PATH.read_text(encoding="utf-8"))


def _run_case(case: dict):
    settings = Settings(vision_provider="demo")
    return asyncio.run(
        analyze(
            settings,
            mode="meal",
            image_bytes=None,
            text=case["input_text"],
            locale="zh-CN",
        )
    )


def test_eval_cases_exist():
    cases = _load_cases()
    assert len(cases) >= 10
    assert all(case.get("expect_items") for case in cases)


def test_name_hit_rate():
    total = 0
    hit = 0
    misses: list[str] = []
    for case in _load_cases():
        resp = _run_case(case)
        matched = {item.name.zh for item in resp.items if item.status == "matched"}
        for expected in case["expect_items"]:
            total += 1
            if expected in matched:
                hit += 1
            else:
                misses.append(f"{case['id']} -> {expected}")
    rate = hit / total
    assert rate >= HIT_RATE_MIN, f"命中率 {rate:.1%} < {HIT_RATE_MIN:.0%}；未命中：{misses}"


def test_kcal_within_tolerance():
    failures: list[str] = []
    for case in _load_cases():
        expect = case.get("expect_kcal")
        if not expect:
            continue
        actual = _run_case(case).totals.kcal
        tol = case.get("tolerance", KCAL_TOLERANCE)
        err = abs(actual - expect) / expect
        if err > tol:
            failures.append(
                f"{case['id']}：实际 {actual}kcal vs 期望 {expect}kcal（误差 {err:.1%} > {tol:.0%}）"
            )
    assert not failures, "热量超出容差：" + "；".join(failures)
