# -*- coding: utf-8 -*-
"""多 agent 包（只含角色 agent，不含编排）。

编排逻辑在 backend/engine.py 的 DeepResearch。
本模块只做导出，不放可执行块；包级自检 demo 见 __main__.py。
"""

from __future__ import annotations

from .base import BaseAgent
from .judge import JudgeAgent
from .keyword import KeywordAgent
from .report import ReportAgent
from .summary import SummaryAgent

__all__ = [
    "BaseAgent",
    "KeywordAgent",
    "SummaryAgent",
    "JudgeAgent",
    "ReportAgent",
]
