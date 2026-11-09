"""ReaderAgent 阅读抽取:抓网页正文 → 逐来源抽取要点(失败降级用搜索 summary)。

要点抽取按批进行(每次 LLM 调用处理一批来源):推理模型每次调用都有
数十秒的思考开销,批量可把抽取调用量降低 BATCH_SIZE 倍,显著缩短总耗时。
"""
import asyncio
import re

import httpx
from bs4 import BeautifulSoup

from config import CONFIG

from .base import BaseAgent


class ReaderAgent(BaseAgent):
    name = "reader"
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    BATCH_SIZE = 4          # 每次 LLM 抽取调用处理的来源数
    FETCH_CONCURRENCY = 8   # 抓页并发
    LLM_CONCURRENCY = 6     # 批量抽取并发

    async def fetch_page(self, url: str) -> str:
        """抓页面并抽正文文本,截断到 page_char_limit;重试 1 次后抛异常(由调用方降级)。"""
        last_err = None
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
                    resp = await client.get(url, headers=self.HEADERS)
                    resp.raise_for_status()
                    if len(resp.content) > 2_000_000:
                        raise ValueError("页面过大(>2MB),跳过抓取")
                soup = BeautifulSoup(resp.text, "html.parser")
                for tag in soup(["script", "style", "noscript", "nav", "header", "footer", "aside", "iframe", "form"]):
                    tag.decompose()
                text = soup.get_text("\n")
                text = re.sub(r"\n{2,}", "\n", text)
                text = re.sub(r"[ \t]+", " ", text)
                text = text.strip()
                if len(text) < 80:
                    raise ValueError("正文过短,可能是纯脚本渲染页")
                return text[: CONFIG.page_char_limit]
            except Exception as e:
                last_err = e
                if attempt == 0:
                    await asyncio.sleep(0.5)
        raise RuntimeError(str(last_err))

    async def extract_notes(self, sources: list, topic: str) -> dict:
        """一批来源 → {sid: 要点};失败抛 AgentError(由调用方整批降级)。"""
        blocks = [f"[{s.sid}]《{s.title}》\n{s.content}" for s in sources]
        system = "你是资料抽取助手:从多个网页正文中,逐个抽取与研究主题相关的关键事实要点。"
        user = (
            f"研究主题:{topic}\n\n"
            f"以下是 {len(sources)} 个来源的正文(截断):\n\n" + "\n\n".join(blocks) + "\n\n"
            "## 任务\n"
            "对每个来源输出不超过 120 字的要点:保留数字、日期、机构名、结论句;"
            "与主题无关的来源,要点写「与主题基本无关」。\n"
            '只输出 JSON:{"notes": {"<来源编号>": "要点", …}},编号用方括号里的数字。'
        )
        raw = await self.call_llm(system, user, temperature=0.1, max_tokens=4096)
        notes = self.parse_json(raw).get("notes", {})
        return {str(k): str(v).strip() for k, v in notes.items() if str(v).strip()}

    async def run(self, sources: list, topic: str) -> None:
        """并发抓正文;抓到的按批做 LLM 抽取,抓取/抽取失败均降级用 summary。"""
        sem_fetch = asyncio.Semaphore(self.FETCH_CONCURRENCY)

        async def fetch(s) -> None:
            async with sem_fetch:
                try:
                    s.content = await self.fetch_page(s.url)
                    s.fetched = True
                except Exception as e:
                    s.fetched = False
                    s.note = s.summary or "(无摘要)"
                    await self.send(
                        "fetch_fail",
                        sid=s.sid, title=s.title, url=s.url,
                        reason=str(e)[:120], fallback="已降级使用搜索摘要",
                    )

        await asyncio.gather(*(fetch(s) for s in sources))

        fetched = [s for s in sources if s.fetched]
        sem_llm = asyncio.Semaphore(self.LLM_CONCURRENCY)
        batches = [fetched[i : i + self.BATCH_SIZE] for i in range(0, len(fetched), self.BATCH_SIZE)]

        async def extract(batch) -> None:
            async with sem_llm:
                try:
                    notes = await self.extract_notes(batch, topic)
                except Exception as e:
                    self.log.warning("批量抽取失败,整批降级用摘要: %s", e)
                    notes = {}
                for s in batch:
                    s.note = notes.get(str(s.sid)) or s.summary or s.content[:500]
                    await self.send("read", sid=s.sid, title=s.title, url=s.url,
                                    fetched=True, note=(s.note or "")[:200])

        if batches:
            await asyncio.gather(*(extract(b) for b in batches))
