"""ResearchOrchestrator：唯一的状态持有者，也是唯一知道"出事该往哪降级"的地方。

Agent 一律无状态、只负责一次 LLM 调用；轮次、并发、补检、降级、落盘全在这里。
流程见需求文档 5.3，降级表见 6.3。**任何一环失败都不允许整个任务失败**（决策 11）：
下面每个环节都是"失败就换一条路把这一轮走完，并在 process 里留痕"。

它不继承 `BaseAgent`：这里一次 LLM 调用都没有，只有调度。
"""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Iterable
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from app.agents.judge import JudgeAgent
from app.agents.plan import PlanAgent
from app.agents.report import ReportAgent
from app.agents.summary import SummaryAgent
from app.config import (
    BATCH_CONCURRENCY,
    MAX_CONCURRENT_TASKS,
    MAX_ROUNDS,
    OUTPUT_DIR,
    SEARCH_COUNT_FOLLOWUP,
    SEARCH_COUNT_INITIAL,
)
from app.models import (
    Conclusion,
    Event,
    JudgeRecord,
    ReportResult,
    SearchRecord,
    Section,
    Source,
    TaskState,
)
from app.render import render_report
from app.tools.confidence import compute_confidence
from app.tools.search import search

NO_RESULT_TEXT = "未检索到公开资料。"

# 事件流里唯一有特殊含义的 stage：它是最后一条，SSE 端点收到就收尾。
STAGE_DONE = "done"

# 产物目录按**项目根**定位，而不是进程当前目录：从别处跑也不会把 output/ 建到奇怪的地方。
# OUTPUT_DIR 给绝对路径时，Path 的 `/` 运算会让它原样胜出，正好。
_OUTPUT_ROOT = Path(__file__).resolve().parent.parent / OUTPUT_DIR

# 同时最多 2 个研究任务（需求文档 6.4）。信号量必须等到事件循环里第一次用再建：
# 模块导入时就建会绑死到当时的 loop，`asyncio.run()` 跑第二遍直接 RuntimeError。
_tasks_sem: asyncio.Semaphore | None = None


def _get_tasks_sem() -> asyncio.Semaphore:
    global _tasks_sem
    if _tasks_sem is None:
        _tasks_sem = asyncio.Semaphore(MAX_CONCURRENT_TASKS)
    return _tasks_sem


def _artifact_path(task_id: str) -> Path:
    return _OUTPUT_ROOT / f"{task_id}.json"


def _register(state: TaskState, sources: list[Source]) -> list[Source]:
    """给检索到的来源编全局号，并按 URL 复用已见过的编号。

    全局唯一是编排器的责任（它才知道已经发过哪些号），所以 `search` 回来时编号都是 0。
    同一个页面被两个子问题命中时只占一个号——否则参考来源里会出现两条一模一样的 URL，
    「来源数量」也会被同一篇文章重复计算，置信度就虚高了。
    """
    fresh: list[Source] = []
    for source in sources:
        known = state.by_url.get(source.url)
        if known is None:
            state.source_seq += 1
            source.id = state.source_seq
            state.by_url[source.url] = source
            state.sources.append(source)
            known = source
        fresh.append(known)
    return fresh


def _emit(state: TaskState, stage: str, message: str, data: dict | None = None) -> None:
    """发一条过程事件。研究过程中**实时**推给 SSE 端点（验收项 8）。

    追加与唤醒之间没有 await，所以在并发协程里发事件既不会串号也不会漏唤醒。
    """
    state.events.append(Event(seq=len(state.events) + 1, stage=stage, message=message, data=data or {}))
    state.wakeup.set()


def _no_result_section(question: str) -> Section:
    """博查 0 条（或调不通）：该节如实保留，但不花一次 LLM 调用去写"我没搜到"。

    这样这一节的 refs 必然是空的，置信度自然记 0.00（需求文档 6.3）。
    """
    return Section(sub_question=question, text=NO_RESULT_TEXT, refs=[])


def _degraded_section(question: str, sources: list[Source]) -> Section:
    """`SummaryAgent` 失败：本节仅保留来源标题与 URL 列表（需求文档 6.3）。

    列表里**不带 `[n]`**——分节正文里出现行内编号就违反验收项 4，编号一律由程序拼在节末。
    """
    listing = "\n".join(f"- {s.title} — {s.url}" for s in sources)
    return Section(
        sub_question=question,
        text=f"本节仅保留来源标题与 URL 列表：\n{listing}",
        refs=[s.id for s in sources],
    )


def _fallback_report(sections: list[Section]) -> ReportResult:
    """`ReportAgent` 失败：拿分节正文直接拼成结论，六章节结构仍然完整（需求文档 6.3）。

    `ReportResult` 要求至少一条结论，所以一个分节都没有时也得给一条占位。
    """
    return ReportResult(
        summary="报告综合环节失败，以下内容由各分节正文直接拼接而成。",
        conclusions=[Conclusion(text=s.text, refs=s.refs) for s in sections]
        or [Conclusion(text=NO_RESULT_TEXT)],
        confidence_note="报告综合环节失败，未能对结论成因做整体说明。",
    )


async def _gather(coros: Iterable[Awaitable[Any]]) -> list[Any]:
    """一批协程按并发上限跑完。`gather` 保序，所以结果与传入顺序一一对应。"""
    sem = asyncio.Semaphore(BATCH_CONCURRENCY)

    async def limited(coro: Awaitable[Any]) -> Any:
        async with sem:
            return await coro

    return list(await asyncio.gather(*(limited(coro) for coro in coros)))


class ResearchOrchestrator:
    """持有全部任务状态。

    对外拆成 `submit()` + `run()` 两步：API 层可以先返回 `task_id`，再把 `run()` 丢进后台，
    这样 SSE 才能在研究过程中就开始推事件。
    """

    def __init__(self) -> None:
        self._tasks: dict[str, TaskState] = {}

    def submit(self, topic: str) -> TaskState:
        state = TaskState(task_id=uuid4().hex[:12], topic=topic)
        self._tasks[state.task_id] = state
        return state

    def get(self, task_id: str) -> TaskState | None:
        return self._tasks.get(task_id)

    def peek_events(self, task_id: str, after: int = 0) -> list[Event]:
        """已经发生过的事件里，`seq > after` 的那些。非流式接口/断线重连补发用。"""
        return [e for e in self._tasks[task_id].events if e.seq > after]

    async def stream(self, task_id: str, after: int = 0) -> AsyncIterator[Event]:
        """从 `seq > after` 开始按序产出事件，直到 `done` 为止。给 SSE 端点用。

        先补发已经发生的（客户端往往比任务晚连上，甚至任务结束后才来），再等新的——
        等待走 `wakeup` 信号，不是轮询。生成器在 `done` 之后立刻收尾：那条事件是承诺的最后一条。
        """
        state = self._tasks[task_id]
        while True:
            for event in self.peek_events(task_id, after):
                after = event.seq
                yield event
                if event.stage == STAGE_DONE:
                    return
            # 扫描与 clear 之间没有 await，所以这中间不可能有新事件被漏掉
            state.wakeup.clear()
            await state.wakeup.wait()

    async def run(self, task_id: str) -> TaskState:
        """跑完一个任务。**无论如何都落盘**，最坏情况是一份带 `error` 的部分结果（决策 11）。"""
        state = self._tasks[task_id]
        started = perf_counter()
        state.status = "running"
        try:
            async with _get_tasks_sem():
                await self._research(state)
            state.status = "succeeded"
        except asyncio.CancelledError:  # 被取消（进程退出 / 调用方取消）：落盘的产物必须说实话
            state.status = "failed"
            state.error = "任务被取消"
            raise
        except Exception as exc:  # 未捕获异常：置 failed，但已完成的部分照常返回，不回裸错误码
            state.status = "failed"
            state.error = f"{type(exc).__name__}: {exc}"
        finally:
            state.process.elapsed_ms = int((perf_counter() - started) * 1000)
            _persist(state)
            # 结束事件最后发：它既是给客户端收尾的信号，也让 [done] 之后不再有任何事件
            _emit(
                state,
                STAGE_DONE,
                f"研究结束：{state.status}",
                {
                    "status": state.status,
                    "rounds": state.process.rounds,
                    "sections": len(state.sections),
                    "sources": len(state.sources),
                    "elapsed_ms": state.process.elapsed_ms,
                    "error": state.error,
                },
            )
        return state

    async def _research(self, state: TaskState) -> None:
        plan = await PlanAgent().run(topic=state.topic)
        state.process.steps.append(plan.step)
        questions = plan.value or [state.topic]  # 规划失败 → 原主题作为唯一子问题（需求文档 6.3）
        _emit(
            state,
            "plan",
            f"规划完成：{len(questions)} 个子问题" + ("" if plan.ok else "（规划失败，已回退为原主题）"),
            {"sub_questions": questions, "ok": plan.ok, "error": plan.step.error},
        )

        for round_no in range(1, MAX_ROUNDS + 1):
            state.process.rounds = round_no
            _emit(
                state,
                "round",
                f"第 {round_no} 轮开始（{'首轮检索' if round_no == 1 else '补检'}）：{len(questions)} 个子问题",
                {"round": round_no, "sub_questions": questions},
            )
            await self._round(
                state, questions, round_no, SEARCH_COUNT_INITIAL if round_no == 1 else SEARCH_COUNT_FOLLOWUP
            )

            verdict = await self._judge(state, round_no)
            # 第 3 轮照样判定，只是不再循环（决策 3）：判不充分的结论本身要留在 judge_history 里
            if round_no >= MAX_ROUNDS or verdict is None or verdict.sufficient:
                break
            if not verdict.new_sub_questions:
                break  # 判了不充分却没给出可补的角度：无处可补，不空跑
            questions = verdict.new_sub_questions

        await self._compose(state)

    async def _round(self, state: TaskState, questions: list[str], round_no: int, count: int) -> None:
        """一轮 = 并发检索 → 并发总结。两批各自受并发上限约束（需求文档 6.4）。"""
        found = await _gather([self._retrieve(state, q, round_no, count) for q in questions])
        sections = await _gather(
            [self._summarize(state, q, sources, round_no) for q, sources in zip(questions, found)]
        )
        state.sections.extend(sections)

    async def _retrieve(self, state: TaskState, question: str, round_no: int, count: int) -> list[Source]:
        """检索一个子问题，返回已编好全局号的来源。失败降级为空列表。"""
        queries, hits, error = [question], 0, None
        try:
            outcome = await search(question, count)
            queries, hits = outcome.queries, len(outcome.sources)
            sources = _register(state, outcome.sources)
        except Exception as exc:  # SearchError 是预期内的；别的意外也不该拖垮整个任务
            error = f"{type(exc).__name__}: {exc}"
            sources = []
        state.process.search_queries.append(
            SearchRecord(round=round_no, sub_question=question, queries=queries, hits=hits, error=error)
        )
        _emit(
            state,
            "search",
            f"检索「{question}」命中 {hits} 条" + (f"（降级：{error}）" if error else ""),
            {"round": round_no, "sub_question": question, "queries": queries, "hits": hits, "error": error},
        )
        return sources

    async def _summarize(
        self, state: TaskState, question: str, sources: list[Source], round_no: int
    ) -> Section:
        if not sources:  # 未检索到公开资料：如实保留该节，refs 为空 → 置信度记 0.00
            _emit(
                state,
                "section",
                f"分节完成（未检索到公开资料）：{question}",
                {"round": round_no, "sub_question": question, "refs": [], "status": "no_sources"},
            )
            return _no_result_section(question)

        result = await SummaryAgent().run(sub_question=question, sources=sources)
        state.process.steps.append(result.step)
        section = result.value if result.ok else _degraded_section(question, sources)
        _emit(
            state,
            "section",
            f"分节完成：{question}（引用 {len(section.refs)} 条来源）"
            + ("" if result.ok else "（总结失败，已降级为来源清单）"),
            {
                "round": round_no,
                "sub_question": question,
                "refs": section.refs,
                "status": "ok" if result.ok else "degraded",
                "error": result.step.error,
            },
        )
        return section

    async def _judge(self, state: TaskState, round_no: int) -> Any:
        result = await JudgeAgent().run(topic=state.topic, sections=state.sections)
        state.process.steps.append(result.step)
        verdict = result.value
        if verdict is None:  # 判定失败视为已充分（需求文档 6.3），但失败原因要留痕
            state.process.judge_history.append(
                JudgeRecord(round=round_no, sufficient=True, error=result.step.error)
            )
            _emit(
                state,
                "judge",
                f"第 {round_no} 轮判定失败，按「视为已充分」处理",
                {"round": round_no, "sufficient": True, "new_sub_questions": [], "error": result.step.error},
            )
            return None
        state.process.judge_history.append(
            JudgeRecord(
                round=round_no,
                sufficient=verdict.sufficient,
                missing_angles=verdict.missing_angles,
                reasons=verdict.reasons,
                new_sub_questions=verdict.new_sub_questions,
            )
        )
        _emit(
            state,
            "judge",
            f"第 {round_no} 轮判定："
            + ("已充分，进入综合" if verdict.sufficient else f"不充分，需补检 {len(verdict.new_sub_questions)} 个角度"),
            {
                "round": round_no,
                "sufficient": verdict.sufficient,
                "missing_angles": verdict.missing_angles,
                "new_sub_questions": verdict.new_sub_questions,
            },
        )
        return verdict

    async def _compose(self, state: TaskState) -> None:
        """综合成文 → 程序算置信度 → 渲染。**分节正文原样透传**（决策 8）。"""
        _emit(
            state,
            "compose",
            f"正在综合成文（{len(state.sections)} 个分节 / {len(state.sources)} 条来源）",
            {"sections": len(state.sections), "sources": len(state.sources)},
        )
        result = await ReportAgent().run(topic=state.topic, sections=state.sections, sources=state.sources)
        state.process.steps.append(result.step)
        report = result.value or _fallback_report(state.sections)
        state.confidence = compute_confidence(state.sections, report.conclusions)
        state.report = render_report(state.topic, report, state.sections, state.sources, state.confidence)


def _persist(state: TaskState) -> None:
    """产物落盘：JSON 一份（契约形态）+ Markdown 一份（直接打开就能看）。

    失败/被取消的任务可能压根没渲染出报告，那就只落 JSON——不为了凑一个 .md 而造空文件。
    """
    path = _artifact_path(state.task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state.to_artifact(), ensure_ascii=False, indent=2), encoding="utf-8")
    if state.report:
        path.with_suffix(".md").write_text(state.report.markdown, encoding="utf-8")


if __name__ == "__main__":
    # 测试 demo：真实跑一次完整研究（需要 .env 里的两个 key 与网络），落盘后打印产物摘要
    import logging
    import sys

    sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台默认 cp936，中文输出会乱码
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s | %(message)s")

    def _check_local() -> None:
        """无网络的纯本地逻辑用假数据自检：编号规则与三条降级路径必须自己站得住。"""
        import re

        state = TaskState(task_id="t", topic="主题")
        page_a = Source(id=0, title="A", url="https://a", summary="")
        page_b = Source(id=0, title="B", url="https://b", summary="")
        assert [s.id for s in _register(state, [page_a, page_b])] == [1, 2]
        # 同一个 URL 换个标题再来一次 → 复用编号，不重复登记
        again = _register(state, [Source(id=0, title="A（转载）", url="https://a", summary="")])
        assert [s.id for s in again] == [1] and len(state.sources) == 2 and state.source_seq == 2
        # 缺字段的检索结果照样能生成合法的 Section（置信度会自然记 0.00）
        zero = _no_result_section("问题")
        assert zero.refs == [] and NO_RESULT_TEXT in zero.text
        # SummaryAgent 失败时的降级节：正文里**不得出现行内 [n]**（验收项 4）
        degraded = _degraded_section("问题", [page_a, page_b])
        assert not re.search(r"\[\d+\]", degraded.text), degraded.text
        assert degraded.refs == [1, 2] and "https://a" in degraded.text
        # ReportAgent 失败时的降级报告：结论至少一条，且引用照旧走 refs
        assert _fallback_report([degraded]).conclusions[0].refs == [1, 2]
        assert _fallback_report([]).conclusions, "一个分节都没有时也要给出占位结论"

        # 落盘：有报告时 JSON + .md 两份；没有报告（失败/取消）时只落 JSON，且不许炸
        from app.models import Report

        json_path = _artifact_path(state.task_id)
        md_path = json_path.with_suffix(".md")
        state.report = Report(markdown="# 报告正文")
        _persist(state)
        assert json_path.is_file() and md_path.read_text(encoding="utf-8") == "# 报告正文"
        md_path.unlink()  # 清掉，才能验下面那个分支没写 .md
        state.report = None
        _persist(state)
        assert json_path.is_file() and not md_path.exists()
        json_path.unlink()

        print("orchestrator 本地自检 ok：编号按 URL 复用 / 0 条降级 / 降级节无行内编号 / 失败仍有结论 / 报告落两份")

    async def _check_injected() -> None:
        """验收项 9：**人为**让检索返回空、让 LLM 一律报错，任务仍须产出降级报告而非整体失败。

        注入点只有两个：搜索的 `_fetch` 与 Agent 基类的 `_chat`——它们分别是检索侧与 LLM 侧
        唯一的出口（见各自模块的注释），换掉它们就等于把这两侧的故障按需开关。
        """
        import app.agents.base as base_mod
        import app.tools.search as search_mod

        real_fetch, real_chat = search_mod._fetch, base_mod.BaseAgent._chat
        real_backoff = base_mod.BaseAgent.RETRY_BACKOFF

        async def empty_fetch(query: str, count: int) -> dict:
            return {"code": 200, "data": {"webPages": {"value": []}}}

        async def broken_chat(self: Any, messages: list[dict[str, str]]) -> str:
            raise RuntimeError("注入的 LLM 故障")

        search_mod._fetch = empty_fetch
        base_mod.BaseAgent._chat = broken_chat
        base_mod.BaseAgent.RETRY_BACKOFF = (0.0, 0.0)  # 注入测试不白等 1s/3s 退避
        try:
            orchestrator = ResearchOrchestrator()
            # 主题特意用疑问句收尾：这样 search 的「0 条 → 用改写词重试一次」也会一起被验到
            state = orchestrator.submit("人为注入的降级测试是什么")
            await orchestrator.run(state.task_id)
        finally:
            search_mod._fetch, base_mod.BaseAgent._chat = real_fetch, real_chat
            base_mod.BaseAgent.RETRY_BACKOFF = real_backoff
        # 注入测试的产物不留盘。**放在断言之前**：断言一旦失败就不会走到后面，文件白留一份
        _artifact_path(state.task_id).unlink()
        _artifact_path(state.task_id).with_suffix(".md").unlink(missing_ok=True)

        assert state.status == "succeeded" and state.error is None, state.error  # 不准整体失败
        # 三个 LLM 环节真的都失败了——证明注入生效，不是"恰好没跑到"
        assert [(s.agent, s.ok) for s in state.process.steps] == [
            ("plan", False),
            ("judge", False),
            ("report", False),
        ], state.process.steps
        # 规划失败 → 回退原主题；检索 0 条 → 该节如实保留、refs 为空；原词与改写词都留痕
        assert state.process.search_queries[0].sub_question == "人为注入的降级测试是什么"
        assert state.process.search_queries[0].hits == 0
        assert len(state.process.search_queries[0].queries) == 2  # 原词 + 改写词，都留痕
        assert state.sections[0].text == NO_RESULT_TEXT and state.sections[0].refs == []
        # 判定失败 → 视为已充分，且失败原因留在 judge_history 里（决策 3、需求文档 6.3）
        verdict = state.process.judge_history[0]
        assert verdict.sufficient is True and "LLMCallError" in (verdict.error or ""), verdict
        # 降级报告仍须六章节齐全；无来源的分节记 0.00，权重为 0 不参与整体分
        assert state.confidence.overall == 0.0 and state.confidence.level == "低", state.confidence
        assert all(
            chapter in state.report.markdown
            for chapter in ("## 摘要", "## 关键结论", "## 分节正文", "## 遗留问题", "## 参考来源", "## 置信度说明")
        ), state.report.markdown
        assert "本节来源：无" in state.report.markdown

        print("\n--- 注入降级后实际产出的报告 ---\n" + state.report.markdown + "\n--- 报告结束 ---")
        print("orchestrator 降级自检 ok：检索为空 + LLM 全报错，任务仍 succeeded 并产出六章节报告")

    async def _check_stream() -> None:
        """无网络自检事件流（验收项 8 的机制）：先补发 / 新事件靠唤醒而非轮询 / done 收尾 / 断线续传。"""
        orchestrator = ResearchOrchestrator()
        state = orchestrator.submit("主题")
        _emit(state, "plan", "规划完成", {"n": 1})

        started = perf_counter()
        seen: list[str] = []

        async def consume() -> None:
            async for event in orchestrator.stream(state.task_id):
                seen.append(f"{perf_counter() - started:.2f}s {event.stage}")

        consumer = asyncio.create_task(consume())
        await asyncio.sleep(0.05)
        # 客户端比任务晚连上是常态：连上先收到已经发生过的
        assert [s.split()[1] for s in seen] == ["plan"], seen

        _emit(state, "search", "检索完成")
        await asyncio.sleep(0.05)
        # 中间这条是"等"来的，不是研究跑完一次性给的——这就是它和事后读 process 的区别
        assert [s.split()[1] for s in seen] == ["plan", "search"], seen

        _emit(state, STAGE_DONE, "研究结束")
        await asyncio.wait_for(consumer, timeout=1)  # 卡住说明 done 之后没收尾，SSE 会永不断开
        assert [s.split()[1] for s in seen] == ["plan", "search", "done"], seen

        # 任务早就结束了才连上来的客户端：从头补发全部（读到 done 即收尾）
        assert [e.stage async for e in orchestrator.stream(state.task_id)] == ["plan", "search", "done"]
        # 断线重连：报上次收到的 seq，只补后面的
        assert [e.stage async for e in orchestrator.stream(state.task_id, after=1)] == ["search", "done"]

        print("orchestrator 事件流自检 ok：先补发已发生的 / 新事件等唤醒 / done 收尾 / 按 seq 续传")

    async def _demo() -> None:
        orchestrator = ResearchOrchestrator()
        state = orchestrator.submit("年轻人为什么爱熬夜")
        print("task_id:", state.task_id)
        started = perf_counter()

        async def watch() -> None:
            """与研究并发消费事件流：打出每条事件的**到达时刻**，证明事件是边跑边推的。"""
            async for event in orchestrator.stream(state.task_id):
                print(f"  [{perf_counter() - started:6.1f}s] {event.stage:<8} {event.message}")
            print(f"  事件流结束（共 {len(state.events)} 条）")

        await asyncio.gather(orchestrator.run(state.task_id), watch())

        print(f"\n状态: {state.status} | 轮次: {state.process.rounds} | "
              f"分节: {len(state.sections)} | 来源: {len(state.sources)} | 耗时: {state.process.elapsed_ms} ms")
        if state.confidence:
            print(f"置信度: {state.confidence.overall}（{state.confidence.level}）"
                  f" 截止 {state.confidence.info_cutoff} | 分节得分 {[(c.sub_question[:12], c.score) for c in state.confidence.per_section]}")
        for record in state.process.search_queries:
            print(f"  第{record.round}轮 命中 {record.hits:>2} 条 | {record.sub_question} | 词={record.queries}"
                  + (f" | {record.error}" if record.error else ""))
        for record in state.process.judge_history:
            print(f"  第{record.round}轮判定 sufficient={record.sufficient} 补检={record.new_sub_questions}"
                  + (f" | {record.error}" if record.error else ""))
        for step in state.process.steps:
            print(f"  step[{step.agent}] ok={step.ok} {step.elapsed_ms}ms attempts={step.attempts} {step.error or ''}")
        print("落盘:", _artifact_path(state.task_id), "|", _artifact_path(state.task_id).with_suffix(".md"))
        print("\n" + (state.report.markdown if state.report else f"（没有报告：{state.error}）"))

    async def _main() -> None:
        # 同一个 loop 里跑：信号量是进程级、绑 loop 的，两个 asyncio.run 会把它绑到前一个 loop 上
        await _check_injected()  # 先跑注入降级（无网络），再验事件流，最后才是真实研究
        await _check_stream()
        await _demo()

    _check_local()  # 先跑本地，网络挂了也不至于连降级路径都没验
    asyncio.run(_main())
