"""博查 Web Search 封装——**tool，不是 Agent**（CLAUDE.md 决策 1）。

不含任何 LLM 推理，因此不继承 `BaseAgent`：基类的提示词渲染、空输出重试、JSON 过闸
对它都没有意义。产物是 `Source` 列表，全局编号从调用方给的 `start_id` 续起——编号必须
跨轮次、跨子问题唯一，而编排器是唯一的状态持有者，所以起点由它给，不由本模块自己攒。

以返回的 `summary` 作为"读到的内容"，**不抓网页正文**（需求文档第 5 节）。
"""

import re
from dataclasses import dataclass, field

import httpx

from app.config import BOCHA_API_KEY, BOCHA_BASE_URL
from app.models import Source

SEARCH_TIMEOUT_SECONDS = 30.0

# 「0 条则用改写后的关键词重试 1 次」（需求文档 6.3）：去掉疑问句尾巴，让搜索引擎拿到更
# 接近关键词的串。ponytail: 纯正则改写，不动语序也不换同义词，够用；明显不够再考虑 LLM 改写。
_QUESTION_TAIL = re.compile(r"[，,。]?\s*(有哪些|是什么|怎么样|为什么|如何|哪些|多少|什么)\s*[？?]?$")


class SearchError(RuntimeError):
    """检索侧失败（未配 key / HTTP 错 / 博查 code 非 200）。编排器据此降级该子问题。"""


@dataclass
class SearchOutcome:
    """一次检索的结果。

    带上 `queries` 是给 `process.search_queries` 用的（验收项 5）：0 条重试时实际发出去的
    是改写后的词，编排器得如实记录"搜了什么"，而不是记原始子问题。
    """

    sources: list[Source] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)


def _rewrite(query: str) -> str:
    """去掉疑问句尾巴。改不动就原样返回，调用方据此决定是否值得重试。"""
    return _QUESTION_TAIL.sub("", query).strip(" ？?，,。")


def _to_sources(payload: dict) -> list[Source]:
    """博查响应 → `Source` 列表，同一响应内按 URL 去重。

    去重是为了不让同一个页面拿到两条记录——否则参考来源里会出现两条一模一样的 URL，
    「每个 `[n]` 唯一对应一个 URL」就名存实亡了。

    `id` 一律填 0：全局编号要对照"已经发过哪些号"，只有编排器知道，由它统一分配。
    """
    pages = ((payload.get("data") or {}).get("webPages") or {}).get("value") or []
    sources: list[Source] = []
    seen: set[str] = set()
    for page in pages:
        url = (page.get("url") or "").strip()
        if not url or url in seen:  # 没 URL 的来源引用了也追不回去，直接丢
            continue
        seen.add(url)
        sources.append(
            Source(
                id=0,
                title=(page.get("name") or url).strip(),
                url=url,
                # 博查偶尔不给 summary，退到 snippet；再没有就是空串，由 SummaryAgent 自己看着办
                summary=(page.get("summary") or page.get("snippet") or "").strip(),
            )
        )
    return sources


async def _fetch(query: str, count: int) -> dict:
    """唯一的网络出口。所有失败都折成 `SearchError`，编排器只需要认这一种。"""
    if not BOCHA_API_KEY:
        raise SearchError("BOCHA_API_KEY 未配置，请检查 .env")
    try:
        async with httpx.AsyncClient(timeout=SEARCH_TIMEOUT_SECONDS) as client:
            response = await client.post(
                BOCHA_BASE_URL,
                headers={"Authorization": f"Bearer {BOCHA_API_KEY}"},
                json={"query": query, "summary": True, "count": count},
            )
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as exc:
        raise SearchError(f"博查请求失败: {type(exc).__name__}: {exc}") from exc
    except ValueError as exc:  # response.json() 解析失败
        raise SearchError(f"博查返回非 JSON: {exc}") from exc


async def search(query: str, count: int) -> SearchOutcome:
    """检索一个子问题。`run()` 之外的另一个对外入口，编排器直接 await 它。

    **0 条不算失败**：如实返回空列表，该子问题会被记成「未检索到公开资料」并保留该节。
    只有真的调不通才抛 `SearchError`。
    """
    payload = await _fetch(query, count)
    if payload.get("code") != 200:
        raise SearchError(f"博查返回 code={payload.get('code')}: {payload.get('msg') or payload.get('messages')}")

    sources = _to_sources(payload)
    queries = [query]
    if sources:
        return SearchOutcome(sources=sources, queries=queries)

    rewritten = _rewrite(query)
    if rewritten and rewritten != query:  # 改不动就别拿原话再打一次，白花一次配额
        payload = await _fetch(rewritten, count)
        if payload.get("code") != 200:
            raise SearchError(f"博查返回 code={payload.get('code')}: {payload.get('msg') or payload.get('messages')}")
        queries.append(rewritten)
        sources = _to_sources(payload)
    return SearchOutcome(sources=sources, queries=queries)


if __name__ == "__main__":
    # 测试 demo：真实调用博查（需要 .env 里的 BOCHA_API_KEY 与网络）
    import asyncio
    import logging
    import sys

    sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台默认 cp936，中文输出会乱码
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s | %(message)s")

    def _check_local() -> None:
        """无网络的纯本地逻辑用假数据自检。"""
        payload = {
            "code": 200,
            "data": {
                "webPages": {
                    "value": [
                        {"name": "标题A", "url": "https://a.com/1", "summary": "摘要A"},
                        {"name": "标题B", "url": "https://b.com/2", "snippet": "摘录B"},  # 无 summary 时退到 snippet
                        {"name": "重复页", "url": "https://a.com/1", "summary": "同一个 URL 再来一次"},
                        {"name": "没有 URL", "url": ""},
                    ]
                }
            },
        }
        got = _to_sources(payload)
        assert [s.url for s in got] == ["https://a.com/1", "https://b.com/2"], got  # 重复/无 URL 的被丢弃
        assert all(s.id == 0 for s in got), got  # 编号由编排器分配
        assert got[0].title == "标题A" and got[0].summary == "摘要A"
        assert got[1].summary == "摘录B", got[1]
        assert _to_sources({"code": 200, "data": None}) == []  # data 为 null 不炸

        assert _rewrite("2026 年国内主流 AI Agent 框架有哪些？") == "2026 年国内主流 AI Agent 框架"
        assert _rewrite("LangChain 是什么？") == "LangChain"
        assert _rewrite("Agent 框架") == "Agent 框架"  # 改不动 → 原样返回，不白重试

        print("search.py 本地自检 ok：URL 去重 / 无 URL 丢弃 / summary 退 snippet / 疑问句改写")

    async def _check_retry() -> None:
        """0 条 → 用改写后的词重试 1 次（需求文档 6.3），且两次检索词都如实留痕。"""
        me = sys.modules[__name__]
        real_fetch, calls = me._fetch, []

        async def fake_fetch(query: str, count: int) -> dict:
            calls.append(query)
            return {"code": 200, "data": {"webPages": {"value": []}}}

        me._fetch = fake_fetch
        try:
            out = await search(query="LangChain 是什么？", count=5)
            stuck = await search(query="Agent 框架", count=5)  # 改不动 → 不该白打第二次
        finally:
            me._fetch = real_fetch

        assert calls == ["LangChain 是什么？", "LangChain", "Agent 框架"], calls
        assert out.queries == calls[:2] and out.sources == []  # 0 条如实返回空，不是错误
        assert stuck.queries == ["Agent 框架"]
        print("search.py 0 条自检 ok：改写词二次检索 / 改不动不重试 / 空结果不算失败")

    async def _demo() -> None:
        out = await search(query="天空为什么是蓝色的？", count=10)
        print("实际检索词:", out.queries, "| 命中:", len(out.sources), "条")
        for s in out.sources[:3]:
            print(f"  {s.title} — {s.url}")
            print(f"       {s.summary[:800]}")

    _check_local()  # 先跑本地，断网/没配 key 时至少解析规则验过了
    asyncio.run(_check_retry())
    asyncio.run(_demo())
