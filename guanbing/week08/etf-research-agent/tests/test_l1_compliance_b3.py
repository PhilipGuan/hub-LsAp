# -*- coding: utf-8 -*-
"""L1 测试：B3 合规入口拦截（CLI exit(2) + FastAPI 400 + 带 flag/字段放行）。"""
from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout, redirect_stderr

from fastapi.testclient import TestClient

import main as cli_main


def test_cli_b3_disagree_exits_code_2() -> None:
    """不带 --disclaimer-agree：CLI 必须 exit(2)，并打印合规拦截文案到 stderr。"""
    buf_out, buf_err = io.StringIO(), io.StringIO()
    argv = ["research", "SOXL", "--peers", "5", "--style", "analyst"]
    with redirect_stdout(buf_out), redirect_stderr(buf_err):
        code = cli_main.main(argv)
    assert code == 2
    stderr_text = buf_err.getvalue()
    assert "合规" in stderr_text or "B3" in stderr_text or "免责" in stderr_text


def test_cli_b3_agree_passes_exits_0() -> None:
    """带 --disclaimer-agree：CLI 放行（Dry-Run 占位 exit 0，打印 JSON 有 research_id 字段）。"""
    buf_out, buf_err = io.StringIO(), io.StringIO()
    argv = ["research", "SOXL", "--peers", "5", "--style", "analyst", "--disclaimer-agree", "--json"]
    with redirect_stdout(buf_out), redirect_stderr(buf_err):
        code = cli_main.main(argv)
    assert code == 0
    stdout = buf_out.getvalue()
    # JSON 应该包含 dryrun_SOXL 占位ID
    assert "SOXL" in stdout
    # 没有 stderr 错误
    assert "合规 B3" not in buf_err.getvalue()


def test_api_b3_400_and_pass(fastapi_client: TestClient) -> None:
    # ① 不带 disclaimer_agree → 返回 400 级错误（接口占位返回 JSON error）
    r = fastapi_client.post("/api/etf/SOXL", json={"peers": 5, "style": "analyst"})
    assert r.status_code < 500
    body = r.json()
    assert "合规" in body.get("error", "") or body.get("http_code") == 400

    # ② 带 disclaimer_agree:true → 返回 research_id, status=pending，无 error
    r2 = fastapi_client.post("/api/etf/SOXL", json={"disclaimer_agree": True, "peers": 5, "style": "analyst"})
    assert r2.status_code < 400
    body2 = r2.json()
    assert body2["ticker"] == "SOXL"
    assert body2["status"] == "pending"
