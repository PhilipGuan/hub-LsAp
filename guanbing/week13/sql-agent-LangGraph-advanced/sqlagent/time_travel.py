"""时间旅行（Time Travel）—— 用 LangGraph 原生的检查点历史做「轨迹 / 回放」。

进阶点之三：项目原来靠**手写 trace** 定位失败到哪一跳；接上 checkpointer 后，
`graph.get_state_history(config)` 会原生吐出一整条检查点链（每一跳的状态快照），
既能打印轨迹，也能**从任意一跳重跑**（换提示词 / 换模型做 A/B 复盘）。

术语对照：

- 一跳（hop）= 一个检查点 `StateSnapshot`，含 `values`（当时状态）、`next`（下一节点）、
  `config.configurable.checkpoint_id`（该跳的身份证）；
- 回放 = 指定某个 `checkpoint_id` 再 `invoke(None, config)`，图会从那一跳继续。
"""

from __future__ import annotations

from typing import Any

# 轨迹里最值得看的字段（与 WorkflowState 对应）
_HOP_KEYS = ("question", "tables", "sql", "attempts", "ok", "error", "answer", "review_action")


def list_hops(graph: Any, config: dict) -> list[dict]:
    """按**时间倒序**（最新在前，LangGraph 原生顺序）列出每一跳的可读摘要。"""
    hops: list[dict] = []
    for snap in graph.get_state_history(config):
        values = snap.values or {}
        meta = snap.metadata or {}
        hops.append({
            "checkpoint_id": (snap.config.get("configurable") or {}).get("checkpoint_id"),
            "step": meta.get("step"),
            "source": meta.get("source"),
            "next": list(snap.next or ()),
            **{k: values.get(k) for k in _HOP_KEYS if k in values},
        })
    return hops


def format_hops(hops: list[dict], limit: int | None = None) -> str:
    """把 `list_hops()` 结果渲染成一段纯文本，便于打印/日志。"""
    shown = hops if limit is None else hops[:limit]
    lines = [f"共 {len(hops)} 跳（最新在前）："]
    for i, h in enumerate(shown):
        nxt = ",".join(h.get("next") or []) or "END"
        lines.append(
            f"[{i:02d}] step={h.get('step')} 下一跳={nxt} "
            f"attempts={h.get('attempts')} ok={h.get('ok')}\n"
            f"     SQL: {h.get('sql')}"
        )
    return "\n".join(lines)


def checkpoint_before(graph: Any, config: dict, next_node: str) -> str | None:
    """找到「下一跳将进入 `next_node`」的那个检查点 ID（例如回到 `generate` 之前）。"""
    for snap in graph.get_state_history(config):
        if next_node in (snap.next or ()):
            return (snap.config.get("configurable") or {}).get("checkpoint_id")
    return None


def replay_from(graph: Any, config: dict, checkpoint_id: str, *,
                updates: dict | None = None) -> dict:
    """从指定检查点**回放**：可选先改点状态（A/B 对照），再继续跑到结束。

    Args:
        checkpoint_id: `list_hops()` 里拿到的某一跳 ID。
        updates: 回放前要覆盖的状态（如 `{"sql": "<人工/对照 SQL>"}`）。

    实现要点：直接用该跳 `StateSnapshot` 自带的 `.config`（内含 `checkpoint_ns` /
    `checkpoint_id`）——只靠 `thread_id + checkpoint_id` 拼的 config 会被检查点库拒绝。
    """
    target = None
    for snap in graph.get_state_history(config):
        if (snap.config.get("configurable") or {}).get("checkpoint_id") == checkpoint_id:
            target = snap
            break
    if target is None:
        raise ValueError(f"未找到检查点：{checkpoint_id}")

    cfg = target.config
    if updates:
        # update_state 会在该检查点处**分叉**出新检查点，并返回指向它的新 config
        cfg = graph.update_state(cfg, updates)
    return graph.invoke(None, cfg)
