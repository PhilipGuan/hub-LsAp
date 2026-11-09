"""共享夹具，外加一条**强制**约束：测试不许联网。

「测试全程不联网」是这个项目刻意维持的性质（见 CLAUDE.md）——引擎的三个外部
依赖都可注入，所以整条状态机能在毫秒内跑穿。但这条性质光靠自觉守不住：
`test_missing_api_key_raises` 早先写的是 `LLMClient()`，读的是真实 .env，
在 LLM_API_KEY 为空时「碰巧」通过；一旦填上真 key，它就会去打 DeepSeek 的真实
接口 —— 本机绿、别人机器红，而且性质已经悄悄没了。

所以这里在 httpx 的发送口上装一道闸：任何没被显式放行的真实请求直接报错。
不 patch socket 是因为 pytest-asyncio 自己会建 socket 做事件循环自唤醒，
一刀切会把大量用例误判成联网；httpx 是这里唯一的出网口（博查、抓取、
openai SDK 都走它），堵它既准又不会误伤。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


@pytest.fixture
def bocha_ok() -> dict[str, Any]:
    """真实的博查成功响应（2026-09 抓取），作为结构假设的回归资产。"""
    return load_fixture("bocha_ok.json")


@pytest.fixture(autouse=True)
def _no_network(request, monkeypatch):
    """拦下所有真实 HTTP 请求。万不得已要联网的用例自行加 marker 放行。"""
    if request.node.get_closest_marker("allow_network"):
        return

    def _blocked(self, http_request, *args, **kwargs):
        raise AssertionError(
            f"测试里出现了真实网络请求: {http_request.method} {http_request.url}\n"
            "请注入替身（searcher / llm / fetcher），或给用例加 @pytest.mark.allow_network"
        )

    monkeypatch.setattr(httpx.AsyncClient, "send", _blocked)
    monkeypatch.setattr(httpx.Client, "send", _blocked)
