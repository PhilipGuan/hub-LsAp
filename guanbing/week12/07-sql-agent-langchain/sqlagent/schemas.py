"""结构化输出契约（Pydantic 模型）。

这是原实现里「`chat(..., json_mode=True)` + `json.loads()` + `.get(...)` 兜底」的
LangChain 等价物：**把散落在字符串里的 JSON 约定，升级成有类型、可校验的对象**。

| 原实现 | 本实现 |
|---|---|
| `json.loads(raw).get("tables", [])` + 手写过滤 | `TableSelection`（`tables: list[str]`） |
| `json.loads(raw)` + `data.setdefault(...)` 手工补默认值 | `ScopeJudgement`（字段自带默认值） |
| `d.get("equivalent")` / `d.get("differences") or []` | `SQLJudge` / `ResultJudge` |
| `d.get("faithful")` / `d.get("missing") or []` | `AnswerJudge` |
| `d.get("harmless")` | `TableExtraJudge` |

**关键取舍**：`with_structured_output` 在解析/校验失败时抛出异常，`invoke_structured()`
把它转成 `None` 返回 —— 这不是偷懒，而是**刻意保留原实现「裁判输出不可解析时记 `None`
（不给结论），而不是猜一个」的语义**。schema 只描述「合法输出长什么样」，不替调用方决定降级策略。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TableSelection(BaseModel):
    """Stage 2：选表 Agent 的输出。"""

    tables: list[str] = Field(
        default_factory=list,
        description="回答该问题所需的最小表集合（表名列表）",
    )


class ScopeJudgement(BaseModel):
    """闸门 [B]：范围守门人的输出。"""

    scope: Literal["in", "out", "ambiguous"] = Field(
        default="ambiguous", description="in=库内可答 / out=与库无关 / ambiguous=意图不完整",
    )
    reason: str = Field(default="", description="简短理由")
    tables: list[str] = Field(default_factory=list, description="若为 in，给出可能用到的表名")
    clarify: str = Field(default="", description="若为 ambiguous，给出向用户的反问；否则空字符串")


class SQLJudge(BaseModel):
    """L2：SQL 语义等价裁判的输出。"""

    equivalent: bool = Field(description="待审 SQL 与权威 SQL 在给定问题上是否语义等价")
    differences: list[str] = Field(default_factory=list, description="若不等价，列出关键口径差异")
    score: float | None = Field(default=None, description="可选评分")


class TableExtraJudge(BaseModel):
    """L1 增强：多余表是否无害。"""

    harmless: bool = Field(description="多选这些表是否会导致结果口径改变 / 出错")
    reason: str = Field(default="", description="简短理由")


class ResultJudge(BaseModel):
    """L4 增强：结果集语义等价复核。"""

    equivalent: bool = Field(description="助手结果与权威结果在回答同一问题时是否语义一致")
    differences: list[str] = Field(default_factory=list, description="若不一致，列出关键口径差异")
    reason: str = Field(default="", description="简短理由")


class AnswerJudge(BaseModel):
    """L5：汇总回答是否忠实、要点是否齐全。"""

    covered: list[str] = Field(default_factory=list, description="已覆盖的要点")
    missing: list[str] = Field(default_factory=list, description="遗漏的要点")
    faithful: bool = Field(description="是否与权威执行结果一致、无编造数字")
    reason: str = Field(default="", description="简短理由")
    score: float | None = Field(default=None, description="可选评分")
