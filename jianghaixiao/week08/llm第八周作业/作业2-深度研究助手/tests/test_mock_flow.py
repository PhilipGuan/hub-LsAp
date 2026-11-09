"""不依赖网络的研究流程测试。"""
import asyncio

from backend.engine import DeepResearch


def test_engine_completes_in_mock_mode() -> None:
    result = asyncio.run(DeepResearch("Agent 框架对比").run())
    assert result.report.title
    assert result.report.sections
    assert result.sources
    assert result.report_html.startswith("<!doctype html>")
