"""面向用户的对接层：三道闸门 + 多轮对话 + 结果渲染。

原实现在 notebook 第 9 节。闸门必须加在**生成 SQL 之前**，否则越界问题会被硬编成 SQL：

```
用户输入
  → [A] 追问改写  rewrite_followup()   有历史时把追问改写成自包含问题
  → [B] 范围判定  scope_check()        in / out / ambiguous + 理由
  → [C] 分支
        out       → 直接谢绝（不生成任何 SQL）
        ambiguous → 反问澄清（不生成任何 SQL）
        in        → 交给 LangGraph 流水线 → 表格 / 图表 / 分析
```

`scope_check()` 仍是**结构性闸门**：守门人只输出 JSON，且做**结构兜底** ——
若它声称 `in` 却落不到任何真实表，强制降级为 `ambiguous`。

与原实现的差异：`scope_check` 的 JSON 解析从「`json.loads` + 手工 `setdefault`」
换成 `ScopeJudgement`（Pydantic）；解析失败时同样降级为 `ambiguous`，语义不变。
"""

from __future__ import annotations

import warnings

import matplotlib.pyplot as plt
import pandas as pd
from IPython.display import display

from .db import DBParser
from .gates import OUT_OF_SCOPE_TEMPLATE, rewrite_followup, scope_check  # noqa: F401  (re-export)
from .i18n import text as _t

# 中文字体：避免图表中文缺字 —— 缺字警告会把本机绝对路径写进 notebook 输出
plt.rcParams["font.sans-serif"] = ["Arial Unicode MS", "Heiti SC", "Hiragino Sans GB", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

def render_result(res, max_rows=None):
    """把结果渲染为表格；「1 类别列 + 1 数值列」且行数适中时自动出柱状图。

    max_rows=None（默认）表示全部展示；传入整数则截断并提示可调大。
    """
    rows = res.get("rows")
    if not rows:
        print("（无结果行）")
        return
    cols = res.get("columns") or [f"col{i}" for i in range(len(rows[0]))]
    total = len(rows)
    shown = rows if max_rows is None else rows[:max_rows]
    df = pd.DataFrame(shown, columns=cols)
    display(df)
    if max_rows is not None and total > max_rows:
        print(f"（共 {total} 行，此处显示前 {max_rows} 行；如需全部：render_result(res, max_rows=None)）")

    num_cols = [c for c in cols if pd.api.types.is_numeric_dtype(df[c])]
    cat_cols = [c for c in cols if c not in num_cols]
    chart_max = 50  # 柱子过多就不画图，避免糊成一团
    if num_cols and cat_cols and 1 < len(df) <= chart_max:
        cat, num = cat_cols[0], num_cols[0]
        ax = df.set_index(cat)[num].plot(kind="bar", figsize=(8, 3.5), legend=False)
        ax.set_title(f"{num} by {cat}")
        ax.set_ylabel(num)
        plt.tight_layout()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # 兜底：渲染警告不得写进输出（含本机路径）
            display(ax.get_figure())
        plt.close()


class ChatBI:
    """多轮对话式 BI：追问改写 → 范围判定 →（in 时）确定性流水线。

    `lang`（默认 `zh`）决定闸门话术与流水线结论的语言；默认值时行为与改动前逐字一致。
    """

    def __init__(self, parser: DBParser, max_history: int = 5, lang: str = "zh"):
        self.parser = parser
        self.max_history = max_history
        self.lang = lang
        self.history = []  # [(用户问题, 助手回答)]

    def ask(self, question: str, show_trace: bool = False) -> dict:
        # 局部导入：避免 chatbi ↔ workflow 的循环依赖
        from .workflow.graph import _run_workflow

        trace = {"raw_question": question, "standalone_question": None, "scope": None,
                 "tables": None, "sql": None, "attempts": 0, "ok": False,
                 "columns": None, "rows": None, "answer": None}

        # [A] 追问改写
        standalone = rewrite_followup(question, self.history, lang=self.lang)
        trace["standalone_question"] = standalone
        if show_trace and standalone != question:
            print(f"[A] 追问改写为独立问题：{standalone}")

        # [B] 范围判定（守门人）
        scope = scope_check(standalone, self.parser, lang=self.lang)
        trace["scope"] = scope
        if show_trace:
            print(f"[B] 范围判定：{scope['scope']}（{scope['reason']}）")

        # [C] 分支
        if scope["scope"] == "out":
            trace["answer"] = _t("out_of_scope", self.lang).format(reason=scope["reason"])
        elif scope["scope"] == "ambiguous":
            trace["answer"] = _t("need_clarify_prefix", self.lang) + (
                scope["clarify"] or _t("clarify_fallback", self.lang))
        else:
            res = _run_workflow(standalone, self.parser, verbose=show_trace, lang=self.lang)
            for k in ("tables", "sql", "attempts", "ok", "columns", "rows", "answer"):
                trace[k] = res[k]

        self.history.append((question, trace["answer"]))
        self.history = self.history[-self.max_history:]
        return trace
