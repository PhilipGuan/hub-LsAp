"""Agent 主循环（S6）：规划 → (关键词 → 搜索 → 阅读 → 抽取 → 反思)×N → 综合。

对应 TECH_DESIGN 第 4 节伪代码。关键机制：
- 来源登记表贯穿全流程，结论可追溯；
- 反思三态（resolved / need_more / abandoned）驱动多轮迭代；
- 轮次 / 页面数上限到限即强制综合；LLM 失败将子问题降级 abandoned。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from . import config, prompts, reporter
from .llm import chat_json
from .models import (
    Extraction,
    Judgement,
    Keywords,
    Plan,
    Report,
    Source,
    SourceRegistry,
    SubQuestion,
    TraceLogger,
)
from .tools.reader import read_material
from .tools.search import SearchError, web_search


@dataclass
class RunResult:
    report: Report
    registry: SourceRegistry
    out_dir: Path
    rounds: int
    elapsed_sec: float

    @property
    def conclusion_with_source_rate(self) -> float:
        conclusions = self.report.key_conclusions
        if not conclusions:
            return 0.0
        return sum(1 for c in conclusions if c.refs) / len(conclusions)


class ResearchAgent:
    """深度研究助手：手写 Agent 主循环（循环 + 工具 + 反思判断）。"""

    def __init__(self, topic: str, out_dir: Path | None = None, verbose: bool = True) -> None:
        self.topic = topic.strip()
        self.verbose = verbose
        self.out_dir = out_dir or reporter.make_out_dir(self.topic, root=config.OUTPUT_DIR)
        self.registry = SourceRegistry()
        self.trace = TraceLogger(self.out_dir / "trace.jsonl")
        self.plan: Plan | None = None
        self.total_pages = 0
        self._suggestions: dict[str, list[str]] = {}  # 反思阶段给每个子问题的补检建议

    # ---------- 主流程 ----------

    def run(self) -> RunResult:
        start = time.time()
        self._plan()
        rounds_run = 0
        for round_no in range(1, config.MAX_ROUNDS + 1):
            pending = [s for s in self.plan.subquestions if s.status == "pending"]
            if not pending:
                break
            if self.total_pages >= config.MAX_TOTAL_PAGES:
                self._say("\n[预算] 已达总页面上限，提前进入综合阶段")
                break
            self._say(f"\n=== 第 {round_no}/{config.MAX_ROUNDS} 轮 ===")
            rounds_run = round_no
            for sub in pending:
                self._say(f"\n[子问题 {sub.id}] {sub.question}")
                self._research_sub(sub, round_no)

        report = self._synthesize()
        report_path = reporter.write_outputs(report, self.registry, self.out_dir)
        elapsed = time.time() - start
        self._say(f"\n[完成] 报告已写入：{report_path}")
        return RunResult(
            report=report,
            registry=self.registry,
            out_dir=self.out_dir,
            rounds=rounds_run,
            elapsed_sec=elapsed,
        )

    # ---------- ① 规划 ----------

    def _plan(self) -> Plan:
        self._say(f"[规划] 主题：{self.topic}")
        plan = chat_json(
            prompts.PLAN_SYSTEM, f"研究主题：{self.topic}", Plan, model=config.LLM_MODEL_STRONG
        )
        subs = plan.subquestions[: config.MAX_SUB_QUESTIONS]
        for i, sub in enumerate(subs, start=1):
            sub.id = sub.id or f"Q{i}"
        self.plan = Plan(subquestions=subs)
        self.trace.log("plan", detail={"topic": self.topic, "subquestions": [s.question for s in subs]})
        for sub in subs:
            self._say(f"  - {sub.id} {sub.question}")
        return self.plan

    # ---------- 单个子问题的一轮研究 ----------

    def _research_sub(self, sub: SubQuestion, round_no: int) -> None:
        try:
            queries = self._gen_keywords(sub, round_no)
        except RuntimeError as e:
            self._degrade(sub, f"关键词生成失败：{e}")
            return
        materials = self._search_and_read(sub, queries, round_no)
        if materials:
            self._extract(sub, materials, round_no)
        self._reflect(sub, round_no)

    # ---------- ② 关键词 ----------

    def _gen_keywords(self, sub: SubQuestion, round_no: int) -> list[str]:
        tried = "\n".join(f"- {k}" for k in sub.keywords_tried) or "（无）"
        suggestions = "、".join(self._suggestions.get(sub.id) or []) or "（无）"
        user = (
            f"子问题（{sub.id}）：{sub.question}\n"
            f"已尝试过的关键词（请避开）：\n{tried}\n"
            f"上一轮评审建议的补检方向（可参考）：{suggestions}"
        )
        kw = chat_json(prompts.KEYWORDS_SYSTEM, user, Keywords, model=config.LLM_MODEL_FAST)
        queries = [q.strip() for q in kw.queries if q.strip()][:3]
        sub.keywords_tried.extend(queries)
        self._say(f"  [关键词] {'、'.join(queries) or '（空）'}")
        return queries

    # ---------- ③ 搜索 + 阅读 ----------

    def _search_and_read(self, sub: SubQuestion, queries: list[str], round_no: int) -> list[tuple[Source, str]]:
        """执行检索并"阅读"新页面，返回 (来源, 阅读材料) 列表。"""
        budget = max(0, min(config.PAGES_PER_ROUND, config.MAX_TOTAL_PAGES - self.total_pages))
        materials: list[tuple[Source, str]] = []
        for query in queries:
            if len(materials) >= budget:
                break
            try:
                hits = web_search(query)
            except SearchError as e:
                self.trace.log("warn", round_no, sub_id=sub.id, query=query, error=str(e))
                self._say(f"  [搜索失败] {query}：{e}")
                continue
            self.trace.log("search", round_no, sub_id=sub.id, query=query, hit_count=len(hits))
            self._say(f"  [搜索] {query} → {len(hits)} 条结果")
            for hit in hits:
                if len(materials) >= budget:
                    break
                if self.registry.seen(hit.url):
                    continue
                try:
                    source, _ = self.registry.register(hit)
                except ValueError:
                    continue  # 非法 URL，跳过
                materials.append((source, read_material(hit)))
                self.total_pages += 1
        self.trace.log("read", round_no, sub_id=sub.id, new_pages=[s.sid for s, _ in materials])
        if materials:
            self._say(f"  [阅读] 新页面 {len(materials)} 个（累计 {self.total_pages}）")
        return materials

    # ---------- ④ 抽取 + 反思 ----------

    def _extract(self, sub: SubQuestion, materials: list[tuple[Source, str]], round_no: int) -> None:
        src_lines = "\n\n".join(f"[{s.sid}] {s.title}\n{material}" for s, material in materials)
        user = f"子问题（{sub.id}）：{sub.question}\n\n来源材料：\n{src_lines}"
        try:
            extraction = chat_json(prompts.EXTRACT_SYSTEM, user, Extraction, model=config.LLM_MODEL_FAST)
        except RuntimeError as e:
            self.trace.log("warn", round_no, sub_id=sub.id, stage="extract", error=str(e))
            self._say(f"  [抽取失败] {e}")
            return
        kept = 0
        for finding in extraction.findings:
            finding.related_sub_id = sub.id
            if self.registry.add_finding(finding) is not None:
                kept += 1
        self._say(f"  [抽取] 保留材料 {kept}/{len(extraction.findings)} 条")

    def _reflect(self, sub: SubQuestion, round_no: int) -> None:
        findings = self.registry.findings_by_sub(sub.id)
        tried = "、".join(sub.keywords_tried) or "（无）"
        if findings:
            mat_lines = "\n".join(
                f"- （置信度:{f.confidence}｜来源:{','.join(f.source_ids) or '无'}）{f.content}"
                for f in findings
            )
            material_txt = f"已有材料：\n{mat_lines}"
        else:
            material_txt = "尚未获得任何相关材料。"
        user = (
            f"子问题（{sub.id}）：{sub.question}\n"
            f"已尝试关键词：{tried}\n"
            f"当前为第 {round_no}/{config.MAX_ROUNDS} 轮。\n"
            f"{material_txt}"
        )
        try:
            judgement = chat_json(prompts.REFLECT_SYSTEM, user, Judgement, model=config.LLM_MODEL)
        except RuntimeError as e:
            self._degrade(sub, f"反思判断失败：{e}")
            return
        judgement.sub_id = sub.id
        if judgement.status == "need_more" and round_no >= config.MAX_ROUNDS:
            judgement.status = "abandoned"
            judgement.reason = "；".join(x for x in (judgement.reason, "已达最大轮次，如实收尾") if x)
        # need_more 映射回 pending，等待下一轮补检；resolved/abandoned 终态
        sub.status = "pending" if judgement.status == "need_more" else judgement.status
        if judgement.status == "need_more" and judgement.extra_keywords:
            self._suggestions[sub.id] = judgement.extra_keywords[:3]
        self.trace.log("reflect", round_no, sub_id=sub.id, status=sub.status, reason=judgement.reason)
        self._say(f"  [反思] {sub.id} → {sub.status}（{judgement.reason or '—'}）")

    def _degrade(self, sub: SubQuestion, reason: str) -> None:
        sub.status = "abandoned"
        self.trace.log("warn", sub_id=sub.id, degraded=reason)
        self._say(f"  [降级] {sub.id} → abandoned：{reason}")

    # ---------- ⑤ 综合 ----------

    def _synthesize(self) -> Report:
        self._say("\n[综合] 汇总材料，生成报告…")
        blocks = []
        for sub in self.plan.subquestions:
            findings = self.registry.findings_by_sub(sub.id)
            if findings:
                lines = "\n".join(
                    f"- [来源:{','.join(f.source_ids) or '无'}｜置信度:{f.confidence}] {f.content}"
                    for f in findings
                )
            else:
                lines = "（未获得材料）"
            blocks.append(f"### 子问题 {sub.id}（{sub.question}）｜状态：{sub.status}\n{lines}")
        source_lines = (
            "\n".join(f"[{s.sid}] {s.title}｜{s.url}" for s in self.registry.all_sources()) or "（无）"
        )
        user = (
            f"研究主题：{self.topic}\n\n"
            f"各子问题研究情况：\n{'\n\n'.join(blocks)}\n\n"
            f"来源列表（引用编号以此为准）：\n{source_lines}"
        )
        try:
            report = chat_json(prompts.SYNTH_SYSTEM, user, Report, model=config.LLM_MODEL_STRONG)
        except RuntimeError as e:
            self.trace.log("warn", stage="synthesize", error=str(e))
            report = Report(
                title=f"研究未完成：{self.topic}",
                summary=f"综合生成阶段失败：{e}",
                confidence_notes=["综合阶段 LLM 调用失败，本轮不产出任何结论"],
            )
        self.trace.log(
            "synthesize", detail={"title": report.title, "conclusions": len(report.key_conclusions)}
        )
        return report

    # ---------- 工具 ----------

    def _say(self, msg: str) -> None:
        if self.verbose:
            print(msg, flush=True)
