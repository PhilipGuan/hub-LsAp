"""确定性裁判（L0 / L1 / L3 / L4 硬判据 + L5 数字溯源诊断）—— 纯代码，不调用 LLM。

第一性原理：**可判定 vs 需判断**。数值是否相等、SQL 能否执行、有无写操作、答案里的数字
能否在结果里找到 —— 用代码断言就够，不劳 LLM。只有「这条 SQL 是否忠实表达了问题」
这类语义问题，才交给 `judge/llm.py`。

这里**逐字移植**原实现的 `_norm_cell / normalize_rows / row_equal / judge_scope /
judge_tables / judge_guard / judge_result / judge_number_trace`，一个字都没改。
"""

from __future__ import annotations

import re

_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _norm_cell(v):
    """单元格归一：数值四舍五入到 1e-6，其余转字符串。"""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, (int, float)):
        return round(float(v), 6)
    return None if v is None else str(v).strip()


def normalize_rows(rows):
    """结果集归一：数值容差 + 行序无关（排序后比较）。"""
    if rows is None:
        return None
    return sorted(tuple(_norm_cell(c) for c in r) for r in rows)


def row_equal(a, b) -> bool:
    return normalize_rows(a) == normalize_rows(b)


def judge_scope(item, trace) -> dict:
    """L0：闸门判定是否与权威标签一致。"""
    got = (trace.get("scope") or {}).get("scope")
    ok = got == item["scope"]
    return {"pass": ok, "detail": "" if ok else f"闸门判定 {got}，期望 {item['scope']}"}


def judge_tables(item, trace) -> dict:
    """L1（确定性部分）：选中表是否覆盖权威所需的表。"""
    if item["scope"] != "in":
        return {"pass": True, "detail": "闸门样本，跳过"}
    want, got = set(item["tables"]), set(trace.get("tables") or [])
    missing, extra = want - got, got - want
    msgs = []
    if missing:
        msgs.append(f"缺表 {sorted(missing)}")
    if extra:
        msgs.append(f"多选 {sorted(extra)}")
    return {"pass": not missing, "want": sorted(want), "got": sorted(got),
            "detail": "；".join(msgs) or "集合一致"}


def judge_guard(item, trace) -> dict:
    """L3：只读守卫 + 校验是否按预期放行（是否产出了可执行 SQL）。"""
    if item["scope"] != "in":
        return {"pass": True, "detail": "闸门样本，跳过"}
    ok = bool(trace.get("ok"))
    return {"pass": ok,
            "detail": "可执行" if ok else f"未能产出可执行 SQL：{str(trace.get('error'))[:120]}"}


def judge_result(item, trace) -> dict:
    """L4（确定性部分）：结果集严格等价 —— 数值 / 表格题的硬判据。

    判 FAIL 不等价于「结果错」，只表示「形状/内容与权威不一致」，可能语义正确；
    由 JudgeAgent 升级到 LLM 复核。
    """
    if item["scope"] != "in":
        return {"pass": True, "detail": "闸门样本，跳过"}
    if not trace.get("ok"):
        return {"pass": False, "detail": "执行失败，无结果可判"}
    got, want = trace.get("rows"), item["ref_rows"]
    ok = row_equal(got, want)
    return {"pass": ok, "got": got, "want": want,
            "detail": "结果集严格一致" if ok else f"形状/内容不一致：got={got!r}，want={want!r}"}


def _nums(text):
    return [m.group(0) for m in _NUM_RE.finditer(text or "")]


def _fmt(v):
    try:
        return f"{float(v):g}"
    except (TypeError, ValueError):
        return None


def judge_number_trace(item, trace) -> dict:
    """L5 诊断（确定性）：答案里的数字能否在「结果集 / 派生合计 / 问题」里找到来源。

    这是**诊断信号**，不参与 pass/fail —— 命中只代表「疑似编造」，需人工/LLM 复核。
    为抑制误报，除「结果集数字 + 问题数字」外再做两类豁免：
    1. **派生合计豁免**：把每个数值列的总和也放进来源池（如客服客户数 21+20+18=59）；
    2. **名次/编号豁免**：整数值落在 [1, 结果行数] 内，视为列表序号（如「1. Rock 2. Latin」）。
    """
    if item["scope"] != "in" or not trace.get("rows"):
        return {"suspicious": []}
    rows = trace["rows"]
    ncol = len(rows[0]) if rows else 0

    pool = set()
    for r in rows:
        for c in r:
            if isinstance(c, (int, float)) and not isinstance(c, bool):
                pool.add(f"{float(c):g}")
            else:
                pool.update(n for n in map(_fmt, _nums(str(c))) if n)
    # 派生合计豁免：数值列整列求和
    for j in range(ncol):
        col = [r[j] for r in rows
               if isinstance(r[j], (int, float)) and not isinstance(r[j], bool)]
        if col:
            pool.add(f"{float(sum(col)):g}")

    q_num = {n for n in map(_fmt, _nums(item["question"])) if n}
    rank_exempt = set(range(1, len(rows) + 1))  # 名次/编号豁免

    suspicious = []
    for tok in _nums(trace.get("answer") or ""):
        val = float(tok)
        if f"{val:g}" in pool or f"{val:g}" in q_num:
            continue
        if val.is_integer() and int(val) in rank_exempt:
            continue
        suspicious.append(tok)
    return {"suspicious": suspicious}
