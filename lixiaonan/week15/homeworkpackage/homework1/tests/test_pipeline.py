"""接口级测试（设计文档 §8）。

运行方式：在项目根目录执行 `pytest tests/ -v`
- 无需 LLM 的用例（始终运行）：路由逻辑、接口参数校验（422）、健康检查
- 依赖真实模型的用例：双引擎分支断言、接口 200 —— 未配置 OPENAI_API_KEY 时自动跳过
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 支持从任意 CWD 运行

# 导入 config 触发 dotenv 加载，以解析后的配置判断是否具备真实模型测试条件
try:
    from config import OPENAI_API_KEY as _API_KEY
except Exception:  # noqa: BLE001 —— 依赖缺失时按无 Key 处理
    _API_KEY = ""

# 演示样例：一正一负一中性 → 期望分支
SAMPLES = [
    ("今天面试通过了，太开心了！", "encourage"),
    ("被老板骂了一顿，很委屈", "comfort"),
    ("明天记得带伞", "neutral_chat"),
]

requires_llm = pytest.mark.skipif(
    not _API_KEY, reason="未配置 LLM_API_KEY / OPENAI_API_KEY，跳过真实模型测试"
)


def _load_run_pipeline(engine):
    """按引擎加载实现；依赖缺失时跳过而非报错。"""
    if engine == "langgraph":
        pytest.importorskip("langgraph")
        pytest.importorskip("langchain_openai")
        from pipelines.langgraph_impl import run_pipeline
    else:
        pytest.importorskip("agents")
        from pipelines.agents_impl import run_pipeline
    return run_pipeline


# ---------- 无需 LLM 的用例 ----------

def test_label_to_branch_mapping():
    """条件路由表本身正确（两实现共用，保证分流等价）。"""
    from prompts import LABEL_TO_BRANCH

    assert LABEL_TO_BRANCH == {
        "positive": "encourage",
        "negative": "comfort",
        "neutral": "neutral_chat",
    }


def test_langgraph_route_and_fallback():
    """LangGraph 条件路由：三类 label 正确分流；非法 label 兜底到 neutral_chat（设计文档 §7）。"""
    pytest.importorskip("langgraph")
    from pipelines.langgraph_impl import route_by_sentiment

    assert route_by_sentiment({"sentiment": {"label": "positive"}}) == "encourage"
    assert route_by_sentiment({"sentiment": {"label": "negative"}}) == "comfort"
    assert route_by_sentiment({"sentiment": {"label": "neutral"}}) == "neutral_chat"
    assert route_by_sentiment({"sentiment": {"label": "weird"}}) == "neutral_chat"


def test_branch_prompts_cover_all_branches():
    from prompts import BRANCH_PROMPTS

    assert set(BRANCH_PROMPTS) == {"encourage", "comfort", "neutral_chat"}
    assert all(p.strip() for p in BRANCH_PROMPTS.values())


def test_api_health():
    pytest.importorskip("fastapi")
    pytest.importorskip("langchain_openai")
    import main
    from fastapi.testclient import TestClient

    resp = TestClient(main.app).get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["engine"] in ("langgraph", "openai-agents")
    assert body["model"]


@pytest.mark.parametrize(
    "payload",
    [
        {"text": ""},  # 空输入
        {"text": "   "},  # 纯空白
        {},  # 缺字段
        {"text": "字" * 501},  # 超长（>500 字）
    ],
)
def test_api_chat_422(payload):
    pytest.importorskip("fastapi")
    pytest.importorskip("langchain_openai")
    import main
    from fastapi.testclient import TestClient

    resp = TestClient(main.app).post("/api/chat", json=payload)
    assert resp.status_code == 422


# ---------- 依赖真实模型的用例 ----------

@requires_llm
@pytest.mark.parametrize("engine", ["langgraph", "openai-agents"])
def test_engine_branches(engine):
    """双引擎跑同一组样例：分支命中正确、输出契约一致（设计文档 FR-5 / §8）。

    三个样例在同一事件循环内批量执行：更快，且避免任何 client 跨循环
    复用的隐患（agents_impl 已改为每次调用独立 client，此处批量仅是习惯）。
    """
    run_pipeline = _load_run_pipeline(engine)

    async def run_all():
        return [await run_pipeline(text) for text, _ in SAMPLES]

    results = asyncio.run(run_all())
    for (_, expected_branch), result in zip(SAMPLES, results):
        assert result.engine == engine
        assert result.branch == expected_branch
        assert result.reply.strip()
        assert result.sentiment.label in ("positive", "negative", "neutral")
        assert 0 <= result.sentiment.confidence <= 1
        assert result.sentiment.reason.strip()
        assert result.elapsed_ms >= 0


@requires_llm
def test_api_chat_ok():
    """接口 200：正向样例 → encourage 分支，结构完整。"""
    pytest.importorskip("fastapi")
    pytest.importorskip("langchain_openai")
    import main
    from fastapi.testclient import TestClient

    resp = TestClient(main.app).post("/api/chat", json={"text": SAMPLES[0][0]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["branch"] == "encourage"
    assert body["reply"].strip()
    assert body["sentiment"]["label"] in ("positive", "negative", "neutral")
