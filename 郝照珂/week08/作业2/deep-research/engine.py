"""Bounded research loop with incremental persistence and citation validation."""
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit
from providers import DemoProvider, LiveProvider


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def strings(value, limit=4):
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(x.strip()[:300] for x in value if isinstance(x, str) and x.strip()))[:limit]


def normalize_claim(claim, valid):
    if not isinstance(claim, dict) or not isinstance(claim.get("text"), str):
        raise ValueError("模型结论结构不符合约定")
    ids = claim.get("source_ids", [])
    if not isinstance(ids, list):
        ids = []
    supplied = [i for i in ids if type(i) is int]
    # Any invalid reference invalidates the claim's attribution rather than silently laundering it.
    clean = list(dict.fromkeys(supplied)) if len(supplied) == len(ids) and all(i in valid for i in supplied) else []
    return {"text": claim["text"][:12000], "source_ids": clean,
            "attribution": "来源支持（未逐句人工核实）" if clean else "模型推断（无有效来源）"}


def normalize_report(report, sources):
    valid = {s["id"] for s in sources}
    if not isinstance(report.get("sections"), list) or not report["sections"]:
        raise ValueError("模型未返回有效报告章节")
    sections = []
    for section in report["sections"][:15]:
        claims = [normalize_claim(c, valid) for c in section.get("claims", [])[:20]]
        sections.append({"heading": str(section.get("heading", "研究发现")), "claims": claims})
    conclusions = [normalize_claim(c, valid) for c in report.get("key_conclusions", [])[:15]]
    if not conclusions:
        raise ValueError("模型未返回关键结论")
    return {"title": str(report.get("title", "研究报告")), "summary": str(report.get("summary", "")),
            "sections": sections, "key_conclusions": conclusions, "open_questions": strings(report.get("open_questions"), 10)}


def run_research(record, store, provider=None):
    def step(kind, detail, round_number=0):
        record["process"].append({"kind": kind, "detail": detail, "round": round_number, "time": now()})
        record["updated_at"] = now()
        store.save(record)

    try:
        record["status"] = "running"
        store.save(record)
        provider = provider or (DemoProvider() if record["mode"] == "demo" else LiveProvider())
        topic = record["topic"]
        plan = provider.plan(topic)
        record["questions"] = strings(plan.get("questions"))
        queries = strings(plan.get("queries"), 3)
        if not queries:
            raise ValueError("规划未产生检索词")
        step("plan", "子问题：" + "；".join(record["questions"]))
        blocks, seen, searched = [], {}, set()
        for rnd in range(1, record["max_rounds"] + 1):
            record["iterations"] = rnd
            for query in queries:
                if query in searched:
                    continue
                searched.add(query)
                record["queries"].append(query)
                step("search", query, rnd)
                hits = provider.search(query, rnd)
                batch = []
                for hit in hits[:5]:
                    parts = urlsplit(hit.get("url", ""))
                    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username:
                        continue
                    url = urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))
                    if url not in seen:
                        source = {**hit, "url": url, "id": len(seen) + 1, "accessed_at": now(),
                                  "reading_method": "固定教学样例" if record["mode"] == "demo" else "搜索 API 摘要"}
                        source["snippet"] = str(source.get("snippet", ""))[:6000]
                        seen[url] = source
                        record["sources"].append(source)
                    batch.append(seen[url])
                step("read", f"读取 {len(batch)} 条搜索摘要：" + ", ".join("[" + str(s["id"]) + "] " + s["url"] for s in batch), rnd)
                if batch:
                    block = provider.summarize(topic, query, batch)
                    valid = {s["id"] for s in batch}
                    block = {"heading": str(block.get("heading", query)),
                             "claims": [normalize_claim(c, valid) for c in block.get("claims", [])[:10]]}
                    blocks.append(block)
                    record["draft"] = blocks
                    step("extract", f"提取 {len(block['claims'])} 条结论：{block['heading']}", rnd)
            decision = provider.judge(topic, blocks, record["sources"], sorted(searched), rnd)
            if type(decision.get("sufficient")) is not bool:
                raise ValueError("补检判断 sufficient 必须是布尔值")
            step("judge", str(decision.get("reason", "未提供判断理由")), rnd)
            if decision["sufficient"]:
                record["stop_reason"] = "资料达到规划要求"
                break
            queries = [q for q in strings(decision.get("queries"), 2) if q not in searched]
            if not queries:
                record["stop_reason"] = "仍有缺口，但没有新的补检关键词"
                break
        else:
            record["stop_reason"] = "达到轮数上限，可能仍存在资料缺口"
        if not record["sources"]:
            raise ValueError("没有获得可引用来源，无法生成有依据的研究报告。")
        step("report", "综合报告并检查来源编号", record["iterations"])
        record["report"] = normalize_report(provider.report(topic, blocks, record["sources"]), record["sources"])
        claims = record["report"]["key_conclusions"]
        cited = sum(bool(c["source_ids"]) for c in claims)
        record["confidence"] = {
            "level": "演示数据，不作事实置信度评估" if record["mode"] == "demo" else "待人工复核",
            "explanation": "引用覆盖率仅表示编号可追溯，不是事实正确率；来源摘要可能过时、片面或相互矛盾。",
            "citation_coverage": f"{cited}/{len(claims)}", "cutoff": now(),
            "limitations": ["仅阅读搜索摘要，未抓取完整网页。", record["stop_reason"], "截止时间表示本次资料访问时间，不保证来源内容最新。"]}
        record["status"] = "completed"
        step("complete", "报告、来源与研究过程已保存", record["iterations"])
    except Exception as exc:
        record["status"] = "failed"
        # Provider failures already redact HTTP responses. Never retain arbitrary server payloads.
        record["error"] = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else "研究过程发生异常，请检查服务日志和配置后重试。"
        step("error", record["error"], record.get("iterations", 0))


def markdown(record):
    report = record["report"]
    lines = ["# " + report["title"], "", "模式：" + record["mode"], "", report["summary"]]
    for section in report["sections"]:
        lines += ["", "## " + section["heading"]]
        for c in section["claims"]:
            lines.append("- " + c["text"] + " " + (" ".join(f"[{i}]" for i in c["source_ids"]) or "【模型推断】"))
    lines += ["", "## 关键结论"]
    for c in report["key_conclusions"]:
        lines.append("- " + c["text"] + " " + (" ".join(f"[{i}]" for i in c["source_ids"]) or "【模型推断】"))
    lines += ["", "## 遗留问题"] + ["- " + s for s in report["open_questions"]]
    lines += ["", "## 置信度与截止时间", record["confidence"]["level"], record["confidence"]["explanation"], record["confidence"]["cutoff"]]
    lines += ["- " + s for s in record["confidence"]["limitations"]]
    lines += ["", "## 来源"]
    lines += [f"[{s['id']}] {s['title']} — {s['url']}（{s['reading_method']}，访问 {s['accessed_at']}）" for s in record["sources"]]
    lines += ["", "## 研究过程"] + [f"- {s['time']} · 第{s['round']}轮 · {s['kind']}：{s['detail']}" for s in record["process"]]
    return "\n\n".join(lines)
