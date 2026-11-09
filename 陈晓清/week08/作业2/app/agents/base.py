"""Agent 基类：四个任务 Agent 的全部共性能力都在这里，子类只实现 `_execute()`。

本文件是「新增 Agent 的唯一范式参考」。子类里**不得**重复实现 LLM 调用、空输出重试、
JSON 解析兜底或模板渲染——一旦重复实现，四个 Agent 的行为就会漂移。
"""

import asyncio
import json
import re
import time
from abc import ABC, abstractmethod
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Any

from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

from app.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
from app.models import StepRecord

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

# 与需求文档 6.3 一致：首次 + 最多 2 次重试，退避 1s、3s。
# 阈值集中在此，子类可按需覆盖 RETRY_BACKOFF（自测就靠它把退避压到 0）。
MAX_LLM_ATTEMPTS = 3
LLM_TIMEOUT_SECONDS = 60.0


class LLMCallError(RuntimeError):
    """LLM 重试耗尽。编排器据此区分「模型侧失败」与「解析/业务失败」。"""


class LLMJsonError(ValueError):
    """JSON 解析兜底仍失败。由编排器按需求文档 6.3 决定降级路径。"""


# ---- 产出收口：报告层面的两条硬性不变量（CLAUDE.md 决策 7）----
# 放基类共享，是因为任何 Agent 各写一份都会漂移：越界编号必须剔除，
# 正文里的行内引用标注只能由程序按 refs 生成。

# 行内引用标注：半角 [] 与全角【】都算
_INLINE_REF = re.compile(r"[\[【]\s*\d+\s*[\]】]")


def strip_inline_refs(text: str) -> str:
    """删掉正文里的行内引用标注，只保留纯文本。

    代价是方括号里的纯数字（比如写成 [2024] 的年份）也会被删掉——宁可少个年份，
    也不能让「行内 [n] 一律由程序按 refs 拼接」这条不变量失效。
    """
    return _INLINE_REF.sub("", text).strip()


def valid_refs(refs: list[int], available_ids: set[int]) -> list[int]:
    """越界编号剔除 + 去重 + 升序。

    「越界」= 模型引用了本次没喂给它的编号，那种编号在报告里找不到对应 URL，必须剔除。
    编号本身就是全局来源编号（见 `models.Source`），所以不需要再做一次映射。
    """
    return sorted({r for r in refs if r in available_ids})


@dataclass
class AgentResult:
    """Agent 的统一返回值。

    `ok=False` 时 `value` 为 None，降级由编排器决定（需求文档 6.3）——
    Agent 自身不知道也不该知道该怎么降级。
    """

    value: Any
    step: StepRecord

    @property
    def ok(self) -> bool:
        return self.step.ok


# LLM 客户端是进程级资源，不属于任何 Agent 实例（Agent 必须无状态）。
_client: AsyncOpenAI | None = None

# 当前 run() 内的 LLM 尝试计数。用 ContextVar 而非实例属性，是为了在
# 「Agent 无状态」与「并发任务互不串号」两个约束下计数；asyncio 下每个 Task 各持一份。
_attempts: ContextVar[int] = ContextVar("llm_attempts", default=0)

# 提示词模板是静态资源，进程内缓存一份，避免每次调用都读盘。
_template_cache: dict[str, str] = {}


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        if not LLM_BASE_URL or not LLM_API_KEY:
            raise RuntimeError("LLM_BASE_URL / LLM_API_KEY 未配置，请检查 .env")
        _client = AsyncOpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)
    return _client


def _load_template(template_name: str) -> str:
    if template_name not in _template_cache:
        path = PROMPTS_DIR / template_name
        if not path.is_file():
            raise FileNotFoundError(f"提示词模板不存在: {path}")
        _template_cache[template_name] = path.read_text(encoding="utf-8")
    return _template_cache[template_name]


class BaseAgent(ABC):
    """模板方法模式：对外只有 `run()`，子类只实现 `_execute()`。"""

    # 子类必须覆盖：写进 process.steps 的 Agent 名，如 "plan" / "summary"
    name: str = "base"

    # 子类可选覆盖：app/prompts/ 下系统提示词的文件名；为 None 则只发用户消息
    system_template: str | None = None

    # 子类可选覆盖：重试退避秒数。自测会覆盖为 (0.0, 0.0)
    RETRY_BACKOFF: tuple[float, ...] = (1.0, 3.0)

    async def run(self, **kwargs: Any) -> AgentResult:
        """唯一对外入口。捕获 `_execute()` 的异常并**如实留痕**，不静默吞掉。

        `**kwargs` 原样转发给 `_execute()`，因此子类 `_execute` 的具名参数才是真正的契约。
        """
        token = _attempts.set(0)
        started = time.perf_counter()
        value: Any = None
        error: str | None = None
        try:
            value = await self._execute(**kwargs)
        except Exception as exc:  # 降级路径由编排器按需求文档 6.3 决定
            error = f"{type(exc).__name__}: {exc}"
        finally:
            attempts = _attempts.get()
            _attempts.reset(token)

        return AgentResult(
            value=value,
            step=StepRecord(
                agent=self.name,
                ok=error is None,
                elapsed_ms=int((time.perf_counter() - started) * 1000),
                attempts=attempts,
                error=error,
            ),
        )

    @abstractmethod
    async def _execute(self, **kwargs: Any) -> Any:
        """子类唯一需要实现的方法：拿输入、调 `call_llm()`、返回结构化结果。"""

    async def call_llm(self, user_prompt: str) -> str:
        """所有 LLM 调用的唯一入口。业务代码不得绕过它直接实例化 LLM 客户端。

        空输出与调用报错都算失败，最多尝试 MAX_LLM_ATTEMPTS 次；仍失败抛 LLMCallError。
        """
        messages: list[dict[str, str]] = []
        if self.system_template:
            messages.append({"role": "system", "content": self.render(self.system_template)})
        messages.append({"role": "user", "content": user_prompt})

        last_error = "LLM 调用未执行"
        for attempt in range(MAX_LLM_ATTEMPTS):
            if attempt and self.RETRY_BACKOFF:
                await asyncio.sleep(self.RETRY_BACKOFF[min(attempt - 1, len(self.RETRY_BACKOFF) - 1)])
            _attempts.set(_attempts.get() + 1)
            try:
                text = await self._chat(messages)
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                continue
            if text.strip():
                return text
            last_error = "LLM 返回空输出"

        raise LLMCallError(f"{self.name} 调用 {MAX_LLM_ATTEMPTS} 次仍失败: {last_error}")

    async def _chat(self, messages: list[dict[str, str]]) -> str:
        """唯一的网络出口。自测覆写它来验证重试逻辑，业务代码不要覆写。"""
        response = await _get_client().chat.completions.create(
            model=LLM_MODEL,
            messages=messages,  # type: ignore[arg-type]
            timeout=LLM_TIMEOUT_SECONDS,
        )
        return (response.choices[0].message.content or "").strip()

    def render(self, template_name: str, **variables: Any) -> str:
        """从 app/prompts/ 读模板并注入变量。

        用 `string.Template`（`$var`）而非 `str.format` / Jinja2：提示词里必然要写
        JSON 输出示例，`{"sub_questions": [...]}` 里的花括号是字面量——
        `str.format` 会 KeyError，Jinja2 则要求把每个示例都转义成 `{{ }}`。
        """
        template = _load_template(template_name)
        try:
            return Template(template).substitute(**variables)
        except KeyError as exc:
            raise ValueError(f"模板 {template_name} 缺少变量 {exc}") from exc

    def parse_model(self, text: str, model: type[BaseModel]) -> Any:
        """`parse_json()` + Pydantic 校验，LLM 输出的唯一过闸口。

        校验失败统一折成 `LLMJsonError`——否则 `ValidationError` 会变成降级表之外的
        第三种异常，编排器没法按需求文档 6.3 决定降级路径。放基类也是为了让四个
        子类不必各写一份 try/except。
        """
        try:
            return model.model_validate(self.parse_json(text))
        except ValidationError as exc:
            raise LLMJsonError(f"{model.__name__} 校验失败: {exc}") from exc

    @staticmethod
    def parse_json(text: str) -> Any:
        """JSON 解析兜底：剥离 Markdown 围栏 → 截取首个 `{`/`[` 到末个 `}`/`]` → 解析。

        切片法失败时再试一次「一行一个 JSON 值」的写法（见 `_merge_json_values`）。
        """
        cleaned = (text or "").strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else ""
            if cleaned.rstrip().endswith("```"):
                cleaned = cleaned.rstrip()[:-3]

        start = min((i for i in (cleaned.find("{"), cleaned.find("[")) if i != -1), default=-1)
        end = max(cleaned.rfind("}"), cleaned.rfind("]"))
        if start == -1 or end <= start:
            raise LLMJsonError(f"未找到 JSON 主体: {text[:120]!r}")
        sliced = cleaned[start : end + 1]
        try:
            return json.loads(sliced)
        except json.JSONDecodeError as exc:
            merged = _merge_json_values(cleaned, start)
            if merged is not None:
                return merged
            # 带上原文：这类失败只有看到模型到底写了什么才诊断得了（截 120 字，够定位）
            raise LLMJsonError(f"JSON 解析失败: {exc} | 原文: {sliced[:120]!r}") from exc


def _merge_json_values(text: str, start: int) -> Any | None:
    """把「一行一个 JSON 值」的写法合并成一个值。合并不出来就返回 None，由调用方决定报错。

    qwen-flash 实测会把子问题列表写成每行一个数组（`["A"]\\n["B"]`）——切片法解析完第一个值
    就撞上 Extra data 整条失败，可那是**同一份数据的另一种写法**，不该降级成一个子问题。
    """
    decoder = json.JSONDecoder()
    values: list[Any] = []
    index = start
    while index < len(text):
        try:
            value, index = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            break
        values.append(value)
        # 跳过值之间的空白与逗号（模型也可能写成 ["A"],\n["B"]）
        while index < len(text) and (text[index].isspace() or text[index] == ","):
            index += 1

    # 0 个＝没救；1 个＝跟切片法结果一样，没有理由改判
    if len(values) < 2:
        return None
    # 拼平：列表摊开，非列表的（比如几段裸字符串）当单个元素
    return [item for value in values for item in (value if isinstance(value, list) else [value])]


if __name__ == "__main__":
    # 测试 demo：真实调用 LLM（需要 .env 里的 LLM_API_KEY 与网络）
    import sys

    sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台默认 cp936，中文输出会乱码

    def _check_parse_json() -> None:
        """无网络的纯本地逻辑用假数据自检：模型写出来的 JSON 五花八门，兜底规则得自己站得住。"""
        parse = BaseAgent.parse_json
        assert parse('["a", "b"]') == ["a", "b"]
        assert parse('```json\n["a", "b"]\n```') == ["a", "b"]
        assert parse('好的，如下：\n["a", "b"]\n以上。') == ["a", "b"]  # 前后夹带解释
        assert parse('{"sub_questions": ["a"]}') == {"sub_questions": ["a"]}
        # 2026-09-10 实测到 6 次里挂 4 次的那种写法：每个子问题各包一个数组、一行一个
        assert parse('["A"]  \n["B"]  \n["C"]') == ["A", "B", "C"]
        assert parse('["A"],\n["B"]') == ["A", "B"]  # 带逗号分隔
        for bad in ('没有 JSON', '["a", "b"', ""):
            try:
                parse(bad)
            except LLMJsonError:
                pass
            else:
                raise AssertionError(f"应抛 LLMJsonError: {bad!r}")
        print("base.py parse_json 自检 ok：围栏 / 夹带解释 / 一行一个数组 / 坏输入报错")

    class DemoAgent(BaseAgent):
        """走通「读模板 → 渲染 → call_llm → parse_json」的最小 Agent。"""

        name = "demo"

        async def _execute(self, topic: str) -> dict:
            text = await self.call_llm(self.render("_selftest.tmpl", topic=topic))
            print("LLM 原始输出:", text)
            return self.parse_json(text)

    async def _demo() -> None:
        # prompts/ 下的正式模板还没写，这里临时造一个：正好示范「提示词住模板文件」的写法
        tmpl = PROMPTS_DIR / "_selftest.tmpl"
        tmpl.write_text('主题：$topic，请返回 {"sub_questions": ["..."]}', encoding="utf-8")
        try:
            result = await DemoAgent().run(topic="2026 年主流 Agent 框架对比")
            print("拆解子问题:", result.value, "| step:", result.step)
        finally:
            tmpl.unlink()

    _check_parse_json()  # 先跑本地，网络挂了也不至于连兜底规则都没验
    asyncio.run(_demo())
