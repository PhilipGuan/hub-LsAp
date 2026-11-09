"""SearcherAgent 检索:生成/优化查询词 → 调博查 Web Search。"""
import asyncio

import httpx

from config import CONFIG

from .base import AgentError, BaseAgent


class SearcherAgent(BaseAgent):
    name = "searcher"
    BOCHA_URL = "https://api.bocha.cn/v1/web-search"

    async def run(self, sub_questions: list, queries: list | None = None) -> tuple:
        """执行一轮检索:未给 queries 时先把子问题优化成查询词,再并发执行所有搜索。

        返回 (queries, results),results 与 queries 一一对应,单项可能是异常。
        """
        if queries is None:
            queries = await self.make_queries(sub_questions)
        results = await asyncio.gather(*(self.search(q) for q in queries), return_exceptions=True)
        return queries, list(results)

    async def make_queries(self, sub_questions: list) -> list:
        """第 1 轮:把子问题改写成更高效的搜索查询。"""
        numbered = "\n".join(f"{i}. {q}" for i, q in enumerate(sub_questions, 1))
        system = "你是搜索专家,擅长把研究问题改写成高效的网络搜索查询。"
        user = (
            f"把下列子问题各改写成 1 条中文搜索查询"
            "(保留关键实体与数字,去掉口语化表达,必要时加限定词如年份/数据/报告):\n"
            f"{numbered}\n\n"
            '只输出 JSON:{"queries": ["…", …]}'
        )
        try:
            raw = await self.call_llm(system, user, temperature=0.2, max_tokens=2048)
            queries = [str(q).strip() for q in self.parse_json(raw).get("queries", []) if str(q).strip()]
        except AgentError as e:
            self.log.warning("查询优化失败,直接用子问题检索: %s", e)
            queries = []
        # 兜底:优化失败时直接用子问题
        if not queries:
            queries = list(sub_questions)
        return queries[: len(sub_questions)]

    async def search(self, query: str) -> list:
        """调博查搜索,返回归一化结果 [{url,title,site_name,summary}];重试 1 次后抛 AgentError。"""
        headers = {
            "Authorization": f"Bearer {CONFIG.bocha_api_key}",
            "Content-Type": "application/json",
        }
        payload = {"query": query, "summary": True, "count": CONFIG.search_count}
        last_err = None
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.post(self.BOCHA_URL, headers=headers, json=payload)
                    resp.raise_for_status()
                    data = resp.json()
                if data.get("code") not in (None, 200, "200"):
                    raise AgentError(f"博查返回异常 code={data.get('code')} msg={data.get('msg')}")
                pages = (data.get("data") or {}).get("webPages") or {}
                items = pages.get("value") or pages.get("results") or []
                results = []
                for it in items:
                    url = it.get("url") or it.get("link") or ""
                    if not url.startswith("http"):
                        continue
                    results.append(
                        {
                            "url": url,
                            "title": (it.get("name") or it.get("title") or url).strip(),
                            "site_name": (it.get("siteName") or it.get("hostName") or "").strip(),
                            "summary": (it.get("summary") or it.get("snippet") or "").strip(),
                        }
                    )
                self.log.info("搜索「%s」命中 %s 条", query, len(results))
                return results
            except Exception as e:
                last_err = e
                self.log.warning("博查搜索失败(第 %s 次): %s", attempt + 1, e)
                if attempt == 0:
                    await asyncio.sleep(1)
        raise AgentError(f"搜索失败:{last_err}")
