"""BaseAgent 抽象基类。

公共能力沉淀于此:LLM 调用封装(重试/超时)、SSE 事件上报、
结构化输出解析(JSON 容错)、运行日志。配置统一从 config.py 导入。
"""
import abc
import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Awaitable, Callable
from urllib.parse import urlparse

import httpx

from config import CONFIG

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


class AgentError(Exception):
    """智能体单点失败(调用方决定跳过还是中止)。"""


# SSE 事件回调:编排器注入,把事件推给前端
EmitFn = Callable[[dict], Awaitable[None]]


@dataclass
class Source:
    """一条来源,引用编号 sid 全局递增,保证与报告引用一一对应。"""

    sid: int
    url: str
    title: str
    site_name: str = ""
    summary: str = ""   # 博查返回的摘要
    content: str = ""   # 抓取的网页正文(失败为空,降级用 summary)
    note: str = ""      # ReaderAgent 抽取的要点
    fetched: bool = False

    @property
    def domain(self) -> str:
        netloc = urlparse(self.url).netloc
        return netloc.removeprefix("www.") or self.site_name or self.url


def sources_digest(sources: list, cap: int = 300) -> str:
    """把来源要点压缩成给 LLM 的摘要文本(Reflector/Writer 共用)。"""
    lines = []
    for s in sources:
        body = (s.note or s.summary or "(无内容)").replace("\n", " ")
        if len(body) > cap:
            body = body[:cap] + "…"
        kind = "正文" if s.fetched else "仅摘要"
        lines.append(f"[{s.sid}]《{s.title}》({s.domain},{kind})\n{body}")
    return "\n\n".join(lines)


class BaseAgent(abc.ABC):
    """所有智能体的抽象基类。"""

    name = "base"

    def __init__(self, emit: EmitFn) -> None:
        self.emit = emit
        self.log = logging.getLogger(f"agent.{self.name}")

    async def send(self, type_: str, **data) -> None:
        """向 SSE 过程流上报事件。"""
        await self.emit({"type": type_, "agent": self.name, **data})

    async def call_llm(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        retries: int = 1,
    ) -> str:
        """OpenAI 兼容 chat/completions 调用:网络/超时重试 1 次;
        推理模型思考 token 计入 max_tokens,输出被截断(finish_reason=length)时自动加大重试一次。"""
        if not CONFIG.llm_api_key or CONFIG.llm_api_key.startswith("请填入"):
            raise AgentError("LLM_API_KEY 未配置,请在项目 .env 中填入 DeepSeek 密钥")
        url = f"{CONFIG.llm_base_url.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {CONFIG.llm_api_key}", "Content-Type": "application/json"}
        base_payload = {
            "model": CONFIG.llm_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
        }
        last_err: Exception | None = None
        attempt = 0
        tokens_cap = max_tokens
        length_retried = False
        while True:
            payload = {**base_payload, "max_tokens": tokens_cap}
            try:
                async with httpx.AsyncClient(timeout=240) as client:
                    resp = await client.post(url, headers=headers, json=payload)
                    resp.raise_for_status()
                    data = resp.json()
            except Exception as e:  # 网络错误 / 4xx5xx / 解析失败统一走重试
                last_err = e
                self.log.warning("LLM 调用失败(第 %s 次): %s", attempt + 1, e)
                if attempt < retries:
                    attempt += 1
                    await asyncio.sleep(1.5)
                    continue
                raise AgentError(f"LLM 调用失败({CONFIG.llm_model}):{last_err}")
            choice = data["choices"][0]
            msg = choice.get("message") or {}
            content = (msg.get("content") or "").strip()
            finish = choice.get("finish_reason")
            if finish == "length" and not length_retried:
                # 思考过长吃掉输出预算 → 加大 max_tokens 重试一次
                length_retried = True
                tokens_cap = min(tokens_cap * 4, 16384)
                self.log.warning("LLM 输出被截断(finish_reason=length),max_tokens 提升至 %s 重试", tokens_cap)
                continue
            if not content:
                reasoning = (msg.get("reasoning_content") or msg.get("reasoning") or "").strip()
                if reasoning and finish != "length":
                    self.log.warning("content 为空,回退使用 reasoning_content(finish_reason=%s)", finish)
                    return reasoning
                raise AgentError(
                    f"LLM 输出为空(finish_reason={finish}),"
                    "可能是 max_tokens 不足或模型异常,请重试")
            return content

    def parse_json(self, text: str) -> dict:
        """容错解析 LLM 返回的 JSON:去 <think> 块与代码围栏,截取首尾大括号之间。"""
        t = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
        if t.startswith("```"):
            t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
            t = re.sub(r"\s*```$", "", t)
        start, end = t.find("{"), t.rfind("}")
        if start == -1 or end <= start:
            raise AgentError(f"LLM 未返回 JSON(输出前 120 字:{text[:120]!r})")
        try:
            return json.loads(t[start : end + 1])
        except json.JSONDecodeError as e:
            raise AgentError(f"LLM 返回的 JSON 解析失败:{e}(输出前 120 字:{text[:120]!r})")

    @abc.abstractmethod
    async def run(self, *args, **kwargs):
        """各智能体的主入口,由子类实现。"""
