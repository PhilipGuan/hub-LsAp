"""人工介入（Human-in-the-loop）驱动。

LangGraph 的 `interrupt()` 只负责**暂停**：它把载荷抛到图外，等外部用
`Command(resume=<值>)` 送回来才继续。本模块就是那个「外部」——把「暂停 → 问人 →
带着答复续跑」的循环封装成一个小驱动器，CLI / notebook / 测试共用。

```python
graph = build_checkpointed_graph(parser, checkpointer=make_checkpointer(), human_review=True)
final = review_loop(graph, {"question": "...", ...}, config, reviewer=my_reviewer)
```

`reviewer(payload) -> dict`：拿到 `{"type": "sql_review", "sql": "...", ...}`，
返回决定字典，如：

- `{"action": "approve"}`
- `{"action": "edit", "sql": "<人工改写后的 SQL>"}`
- `{"action": "reject", "note": "口径不对"}`
"""

from __future__ import annotations

from typing import Any, Callable

from langgraph.types import Command

Reviewer = Callable[[dict], dict]


def pending_interrupt(result: dict) -> dict | None:
    """从 `invoke()` 的返回里取出待处理的 interrupt 载荷；没有则返回 None。"""
    raw = (result or {}).get("__interrupt__")
    if not raw:
        return None
    first = raw[0]
    return getattr(first, "value", first)


def review_loop(
    graph: Any,
    input_state: dict | None,
    config: dict,
    reviewer: Reviewer,
    *,
    verbose: bool = False,
    max_rounds: int = 20,
) -> dict:
    """跑到图不再中断为止：每次中断都交给 `reviewer` 决定，再续跑。

    `max_rounds` 是防呆上界（人工反复 edit 时不至于死循环）。
    """
    result = graph.invoke(input_state, config)
    rounds = 0

    while True:
        payload = pending_interrupt(result)
        if payload is None:
            return result
        rounds += 1
        if rounds > max_rounds:
            raise RuntimeError(f"人工介入轮次超过 {max_rounds}，疑似死循环")

        decision = reviewer(payload) or {}
        if verbose:
            print(f"[HITL] 复核决定：{decision.get('action')}")
        result = graph.invoke(Command(resume=decision), config)
