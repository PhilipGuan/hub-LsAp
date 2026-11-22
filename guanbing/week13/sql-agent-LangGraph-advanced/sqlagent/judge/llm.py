"""LLM 裁判（L2 / L1 增强 / L4 增强 / L5）—— 只在「语义不可判定」处使用，且必须带参照物。

三条规矩（照搬原实现）：
1. 能给参照物就给（问题 / 权威 SQL / 权威结果集）—— 把「你觉得对吗」变成「对照批改」；
2. 只输出 JSON，解析失败记 `None`（不计入 pass/fail），不猜；
3. 裁判只读 trace，不改动流水线。

与原实现的差异：`json.loads(raw)` + `.get(...)` 换成 `schemas.py` 里的 Pydantic 模型，
由 `llm.invoke_structured_messages()` 统一拿到校验过的对象；解析失败同样返回 `None`/空。
"""

from __future__ import annotations

from .. import llm, prompts
from ..schemas import AnswerJudge, ResultJudge, SQLJudge, TableExtraJudge


def judge_sql(item, trace) -> dict:
    """L2：生成的 SQL 与权威 SQL 是否语义等价（LLM）。"""
    if item["scope"] != "in" or not trace.get("sql") or not item["ref_sql"]:
        return {"pass": True, "detail": "闸门样本，跳过", "score": None}
    msgs = prompts.JUDGE_SQL_PROMPT.format_messages(
        q=item["question"], sql=trace["sql"], ref=item["ref_sql"],
    )
    verdict = llm.invoke_structured_messages(SQLJudge, msgs)
    if verdict is None:
        return {"pass": None, "detail": "裁判输出无法解析", "score": None}
    return {"pass": verdict.equivalent, "score": verdict.score,
            "detail": "语义等价" if verdict.equivalent
                      else ("；".join(map(str, verdict.differences)) or "语义不等价")}


def judge_tables_llm(item, trace) -> dict:
    """L1 增强：确定性命中但有「多余表」时，交 LLM 判是否无害。"""
    msgs = prompts.JUDGE_TABLES_PROMPT.format_messages(
        q=item["question"], want=item["tables"],
        extra=sorted(set(trace.get("tables") or []) - set(item["tables"])),
        got=sorted(trace.get("tables") or []),
    )
    verdict = llm.invoke_structured_messages(TableExtraJudge, msgs)
    if verdict is None:
        return {"harmless": None, "reason": "裁判输出无法解析"}
    return {"harmless": verdict.harmless, "reason": verdict.reason}


def judge_result_llm(item, trace) -> dict:
    """L4 增强：确定性结果集比较判 FAIL 时，交 LLM 做「语义等价」复核。

    只在形状/列数不一致等无法用代码判死的情形触发，避免把「语义正确但形状不同」
    误判为错（结果集严格相等只是充分不必要条件）。
    """
    msgs = prompts.JUDGE_RESULT_PROMPT.format_messages(
        q=item["question"], want=item["ref_rows"], got=trace.get("rows"),
    )
    verdict = llm.invoke_structured_messages(ResultJudge, msgs)
    if verdict is None:
        return {"equivalent": None, "reason": "裁判输出无法解析", "differences": []}
    return {"equivalent": verdict.equivalent, "reason": verdict.reason,
            "differences": verdict.differences}


def judge_answer(item, trace) -> dict:
    """L5：汇总回答是否忠实、要点是否齐全（LLM）。"""
    if item["scope"] != "in" or not trace.get("answer"):
        return {"pass": True, "detail": "闸门样本，跳过", "score": None}
    msgs = prompts.JUDGE_ANSWER_PROMPT.format_messages(
        q=item["question"], rows=(trace.get("rows") or [])[:50],
        kp=item.get("ref_answer_keypoints") or [], ans=trace["answer"],
    )
    verdict = llm.invoke_structured_messages(AnswerJudge, msgs)
    if verdict is None:
        return {"pass": None, "detail": "裁判输出无法解析", "score": None}
    ok = bool(verdict.faithful) and not verdict.missing
    return {"pass": ok, "score": verdict.score,
            "detail": "忠实且要点齐全" if ok else f"遗漏 {verdict.missing}；{verdict.reason}"}
