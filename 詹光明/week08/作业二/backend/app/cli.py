"""调试用 CLI —— 开发阶段的主要手段，不依赖 HTTP。

每个子命令对应流水线的一段，方便单独验证：

    python -m app.cli search "<关键词>"     # 检索 + 核对博查响应结构
    python -m app.cli fetch <url>...        # 抓取 + 正文提取
    python -m app.cli plan "<主题>"         # 规划子问题
    python -m app.cli run "<主题>"          # 完整链路
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any


def force_utf8() -> None:
    """Windows 控制台默认 GBK，中文输出会乱码；强制 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)-7s %(name)s | %(message)s",
        stream=sys.stderr,
    )
    # httpx 的 INFO 日志太吵
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


FIXTURES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

# 这两个分支对解析没有价值，但占了响应体积的一大半
_UNUSED_BRANCHES = ("images", "videos")


def trim_bocha_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """裁掉 images / videos，保留完整的结果结构。"""
    data = payload.get("data")
    if not isinstance(data, dict):
        return payload
    trimmed = {k: v for k, v in data.items() if k not in _UNUSED_BRANCHES}
    return {**payload, "data": trimmed}


def save_bocha_fixture(payload: dict[str, Any], name: str) -> Path:
    """把真实响应存成回归夹具 —— 博查结构一旦变化，测试会先发现。"""
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    path = FIXTURES_DIR / (name if name.endswith(".json") else f"{name}.json")
    path.write_text(
        json.dumps(trim_bocha_payload(payload), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path


def _rule(title: str = "") -> None:
    if title:
        print(f"\n{'─' * 4} {title} {'─' * max(0, 60 - len(title))}")
    else:
        print("─" * 70)


# ══════════════════════════════════════════════════════════════
# search
# ══════════════════════════════════════════════════════════════


def cmd_search(args: argparse.Namespace) -> int:
    from .dedup import normalize_url, registrable_domain
    from .search import BochaSearchClient

    async def _run() -> Any:
        async with BochaSearchClient() as client:
            return await client.search(
                args.query,
                count=args.count,
                summary=not args.no_summary,
                keep_raw=args.raw or bool(args.save_raw),
            )

    outcome = asyncio.run(_run())

    if args.json:
        print(json.dumps(outcome.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return 0 if outcome.ok else 1

    _rule(f"检索: {args.query}")
    print(
        f"ok={outcome.ok}  code={outcome.code}  msg={outcome.msg}  "
        f"耗时={outcome.latency_ms}ms  重试={outcome.attempts}"
    )
    print(f"命中路径: {outcome.matched_path}    结果数: {len(outcome.results)}")

    if outcome.top_level_keys:
        print(f"!! 未找到结果列表，实际顶层 key: {outcome.top_level_keys}")
    if outcome.error:
        print(f"错误: {outcome.error}")

    if outcome.raw is not None and args.save_raw:
        path = save_bocha_fixture(outcome.raw, args.save_raw)
        print(f"\n原始响应已保存: {path}")

    if args.raw and outcome.raw is not None:
        _rule("原始响应 JSON")
        print(json.dumps(outcome.raw, ensure_ascii=False, indent=2))
        return 0 if outcome.ok else 1

    if outcome.results:
        _rule("结果列表")
        for i, item in enumerate(outcome.results, 1):
            domain = registrable_domain(item.url)
            published = item.published_at.strftime("%Y-%m-%d") if item.published_at else "无日期"
            print(f"\n[{i}] {item.title}")
            print(f"    站点: {item.site_name or '—'}  域名: {domain}  发布: {published}")
            print(f"    URL : {item.url}")
            if normalize_url(item.url) != item.url:
                print(f"    归一: {normalize_url(item.url)}")
            if item.summary:
                print(f"    摘要({len(item.summary)}字): {item.summary[:100]}…")
            elif item.snippet:
                print(f"    描述: {item.snippet[:100]}")

    return 0 if outcome.ok else 1


# ══════════════════════════════════════════════════════════════
# fetch
# ══════════════════════════════════════════════════════════════


def cmd_fetch(args: argparse.Namespace) -> int:
    from .fetch import Fetcher, make_source

    summary = args.summary or ""
    sources = [
        make_source(sid=f"S{i}", url=url, search_summary=summary or None)
        for i, url in enumerate(args.urls, 1)
    ]

    async def _run() -> Any:
        async with Fetcher() as fetcher:
            return await fetcher.fetch_many(sources)

    results = asyncio.run(_run())

    _rule("抓取结果")
    for source in results:
        print(
            f"{source.sid}  {source.fetch_status.value:<17} "
            f"extractor={(source.extractor or '—'):<14} "
            f"chars={source.content_chars:<6} origin={source.content_origin.value}"
        )
        print(f"      {source.url}")
        if source.fetch_error:
            print(f"      ! {source.fetch_error}")
        if args.show and source.content:
            preview = source.content[: args.show].replace("\n", " ")
            print(f"      预览: {preview}…")

    _rule()
    by_origin: dict[str, int] = {}
    for source in results:
        by_origin[source.content_origin.value] = by_origin.get(source.content_origin.value, 0) + 1
    print(f"共 {len(results)} 条 · 正文来路分布: {by_origin}")

    return 0


# ══════════════════════════════════════════════════════════════
# plan
# ══════════════════════════════════════════════════════════════


def cmd_plan(args: argparse.Namespace) -> int:
    from .agents.planner import plan
    from .agents.reflector import reflect
    from .llm import LLMClient

    async def _run() -> Any:
        async with LLMClient() as llm:
            result = await plan(args.topic, llm, max_sub_questions=args.max_sub_questions)
            if not args.reflect:
                return result, None, None

            # 用假的笔记演示反思 —— 这里不联网，只看 LLM 的契约对不对
            from .models import Note

            fake_note = Note(
                note_id="S1-N1",
                sid="S1",
                qid=result.sub_questions[0].qid if result.sub_questions else "",
                claim="演示用的一条笔记",
            )
            outcome = await reflect(
                args.topic,
                result.sub_questions,
                [fake_note],
                result.queries,
                llm,
                round_no=1,
                max_rounds=args.max_rounds,
            )
            return result, outcome, None

    result, outcome, _ = asyncio.run(_run())

    _rule(f"规划: {args.topic}")
    if result.degraded:
        print("!! 走了模板兜底（LLM 调用失败）")
    print(f"思路: {result.rationale or '（无）'}")

    _rule("子问题")
    for sub in result.sub_questions:
        print(f"[{sub.qid}] {sub.text}")
        if sub.rationale:
            print(f"     理由: {sub.rationale}")
        if sub.initial_queries:
            print(f"     自带检索词: {sub.initial_queries}")

    _rule("首轮检索词")
    for i, query in enumerate(result.queries, 1):
        print(f"  {i}. {query}")

    if outcome is not None:
        _rule("反思（基于一条假笔记）")
        if outcome.degraded:
            print("!! 走了兜底（LLM 调用失败）")
        print(f"LLM 认为足够: {outcome.sufficient}")
        print(f"覆盖率: {outcome.coverage}")
        print(f"缺口: {outcome.gaps}")
        print(f"下轮检索词: {outcome.next_queries}")
        print(f"理由: {outcome.rationale or '（无）'}")

    return 0


# ══════════════════════════════════════════════════════════════
# run
# ══════════════════════════════════════════════════════════════


# 进度打到 stderr，报告打到 stdout —— 这样 `run "主题" > report.md` 拿到的
# 是一份干净的 markdown，进度信息不会混进去。
def _progress(event: Any) -> None:
    """把事件渲染成一行进度。**这是 CLI 与 SSE 共用同一串事件的直接证据。**"""
    from .events import EventType

    payload = event.payload
    kind = event.type
    prefix = f"[{event.round}]" if event.round else "   "

    if kind is EventType.status:
        line = payload.get("message", "")
    elif kind is EventType.plan:
        subs = len(payload.get("sub_questions", []))
        line = f"规划出 {subs} 个子问题，首轮 {len(payload.get('queries', []))} 个检索词"
    elif kind is EventType.search:
        line = f"跳过重复检索词: {payload.get('query')}"
    elif kind is EventType.search_results:
        mark = "" if payload.get("ok") else "!! 失败"
        line = (
            f"检索「{payload.get('query')}」→ {payload.get('found')} 条，"
            f"新增 {payload.get('added')} 条 {mark}"
        )
    elif kind is EventType.fetch:
        line = (
            f"抓取 {payload.get('sid')} {payload.get('status')} "
            f"({payload.get('origin')}, {payload.get('chars')} 字)"
        )
    elif kind is EventType.fetch_failed:
        line = (
            f"抓取失败 {payload.get('sid')} {payload.get('status')} "
            f"→ 降级 {payload.get('fallback')}（{payload.get('message')}）"
        )
    elif kind is EventType.note:
        line = (
            f"笔记 {payload.get('note_id')} [{payload.get('sid')}] {payload.get('claim', '')[:50]}…"
        )
    elif kind is EventType.reflect:
        line = (
            f"反思: LLM 认为{'足够' if payload.get('llm_sufficient') else '不够'} · "
            f"判定 {payload.get('decision')} · {payload.get('detail')}"
        )
    elif kind is EventType.round_end:
        line = (
            f"本轮结束: 新增 {payload.get('new_sources')} 条来源 / "
            f"{payload.get('total_notes')} 条笔记"
        )
    elif kind is EventType.synthesize_start:
        line = "开始综合材料"
    elif kind is EventType.section:
        line = f"生成分节: {payload.get('heading')}"
    elif kind is EventType.confidence:
        line = f"置信度 {payload.get('score')} ({payload.get('level')})"
    elif kind is EventType.error:
        line = f"!! {payload.get('scope')}: {payload.get('message')}"
    elif kind is EventType.cancelled:
        line = "已取消"
    elif kind is EventType.stop:
        line = (
            f"收尾: {payload.get('reason')} · {payload.get('rounds')} 轮 · "
            f"{payload.get('elapsed_s')}s"
        )
    elif kind is EventType.done:
        line = f"完成: {payload.get('sources')} 条来源 · {payload.get('notes')} 条笔记"
    else:
        return

    print(f"{prefix} {line}", file=sys.stderr, flush=True)


def cmd_run(args: argparse.Namespace) -> int:
    from .engine import run_research
    from .models import ResearchRequest

    request = ResearchRequest(
        topic=args.topic,
        max_rounds=args.max_rounds,
        fetch_enabled=not args.no_fetch,
    )

    async def _run() -> Any:
        report = None
        events: list[Any] = []
        async for event in run_research(request, run_id=args.run_id or None):
            events.append(event)
            if not args.quiet:
                _progress(event)
            if event.type.value == "done":
                report = event.payload.get("report")
        return report, events

    report, events = asyncio.run(_run())

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report else 1

    if args.events:
        _rule("事件流")
        for event in events:
            print(f"{event.seq:>4}  r{event.round}  {event.type.value}")

    if report is None:
        print("研究未能产出报告（见上方错误）", file=sys.stderr)
        return 1

    markdown = report.get("markdown", "")
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown, encoding="utf-8")
        print(f"\n报告已写入 {path}", file=sys.stderr)

    # 进度都走的 stderr，所以这里 stdout 是干净的 markdown
    print(markdown)
    return 0


# ══════════════════════════════════════════════════════════════
# 入口
# ══════════════════════════════════════════════════════════════


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description="深度研究助手 · 调试 CLI",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="打印 DEBUG 日志")
    sub = parser.add_subparsers(dest="command", required=True)

    p_search = sub.add_parser("search", help="只跑检索")
    p_search.add_argument("query", help="检索关键词")
    p_search.add_argument("--count", type=int, default=None, help="返回条数（默认取配置）")
    p_search.add_argument("--no-summary", action="store_true", help="不请求长摘要")
    p_search.add_argument("--raw", action="store_true", help="打印原始响应 JSON（核对字段用）")
    p_search.add_argument(
        "--save-raw",
        metavar="NAME",
        default=None,
        help="把裁剪后的原始响应存到 tests/fixtures/NAME.json 作为回归夹具",
    )
    p_search.add_argument("--json", action="store_true", help="以 JSON 输出解析结果")
    p_search.set_defaults(func=cmd_search)

    p_fetch = sub.add_parser("fetch", help="只跑抓取 + 正文提取")
    p_fetch.add_argument("urls", nargs="+", help="要抓取的 URL")
    p_fetch.add_argument(
        "--summary",
        default=None,
        help="模拟博查返回的长摘要。抓取失败时会降级到它，用来演示降级链",
    )
    p_fetch.add_argument("--show", type=int, default=0, help="打印每条正文开头 N 个字符")
    p_fetch.set_defaults(func=cmd_fetch)

    p_plan = sub.add_parser("plan", help="只跑规划（+ 可选反思）")
    p_plan.add_argument("topic", help="研究主题")
    p_plan.add_argument("--max-sub-questions", type=int, default=4, help="拆几个子问题")
    p_plan.add_argument("--max-rounds", type=int, default=3, help="反思时告知的轮次上限")
    p_plan.add_argument("--reflect", action="store_true", help="顺带跑一次反思（用一条假笔记）")
    p_plan.set_defaults(func=cmd_plan)

    p_run = sub.add_parser("run", help="跑完整链路并打印报告")
    p_run.add_argument("topic", help="研究主题")
    p_run.add_argument("--max-rounds", type=int, default=3, help="最多迭代几轮")
    p_run.add_argument("--no-fetch", action="store_true", help="跳过抓取（只验编排与 LLM）")
    p_run.add_argument("--run-id", default=None, help="指定 run_id（默认随机）")
    p_run.add_argument("--out", default=None, help="把报告 markdown 写入文件")
    p_run.add_argument("--json", action="store_true", help="输出报告 JSON 而不是 markdown")
    p_run.add_argument("--events", action="store_true", help="末尾附上事件流清单")
    p_run.add_argument("-q", "--quiet", action="store_true", help="不打印进度")
    p_run.set_defaults(func=cmd_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    force_utf8()
    parser = build_parser()
    args = parser.parse_args(argv)
    setup_logging(args.verbose)

    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
