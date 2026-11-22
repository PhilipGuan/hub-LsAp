"""只读安全守卫：`is_readonly()`（逐字移植，一字未改）。

**这一层刻意不套 LangChain，也不写进提示词。**
LLM 生成的 SQL 不可信，护栏必须做进**代码结构**：即使模型被越狱、即使提示词被改写，
写操作也到不了数据库。这正是原设计文档里的「Structure vs Prompt」主张。

检查顺序（与原实现完全一致）：
1. 去行注释 → 去块注释 → 去首尾分号；
2. 仍含 `;` → 拒绝（不允许N条语句）；
3. 首词必须是 `SELECT` 或 `WITH`；
4. 剔除字符串字面量后再扫写关键字（避免把歌名里的 "update" 误判成语句）。
"""

from __future__ import annotations

import re

_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|truncate|"
    r"attach|detach|pragma|vacuum|reindex|grant|revoke)\b",
    re.IGNORECASE,
)


def is_readonly(sql: str):
    """只允许单条 SELECT / WITH 只读查询，返回 (是否通过, 原因)。"""
    if not sql or not sql.strip():
        return False, "空 SQL"

    s = re.sub(r"--[^\n]*", "", sql)                 # 去行注释
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)      # 去块注释
    s = s.strip().rstrip(";").strip()

    if ";" in s:
        return False, "不允许多条语句"
    if not re.match(r"^(select|with)\b", s, re.IGNORECASE):
        return False, "只允许 SELECT / WITH 只读查询"

    # 剔除字符串字面量后再扫描关键字
    scan = re.sub(r"'(?:[^']|'')*'", "''", s)
    scan = re.sub(r'"(?:[^"]|"")*"', '""', scan)
    m = _FORBIDDEN.search(scan)
    if m:
        return False, f"检测到非只读关键字：{m.group(0)}"
    return True, "ok"
