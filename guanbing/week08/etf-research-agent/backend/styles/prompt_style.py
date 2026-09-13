# -*- coding: utf-8 -*-
"""M5 · PromptStyle 配置（3 风格切换 · Spec §6 M5）。
3 种风格：analyst 券商研报风（偏多空） / institutional 机构投顾（偏风险） / explainer 科普解释（通俗）。
正式实现时把 3 种风格的 prompt snippet 写到 templates/style_snippets/*.jinja2，
这里只做 Enum 定义 + 校验。
"""
from __future__ import annotations

from enum import Enum


class PromptStyle(str, Enum):
    ANALYST = "analyst"
    INSTITUTIONAL = "institutional"
    EXPLAINER = "explainer"


STYLE_DESCRIPTIONS: dict[PromptStyle, str] = {
    PromptStyle.ANALYST: "券商卖方研报风：结论前置、多维论据、主题标签化、风险提示充分。",
    PromptStyle.INSTITUTIONAL: "机构投顾风：以客户风险承受为第一、合规免责重、决策约束明确。",
    PromptStyle.EXPLAINER: "通俗科普风：适合普通用户，避开金融术语，用类比解释 ETF 机制。",
}


def normalize_style(raw: str) -> PromptStyle:
    s = (raw or "analyst").strip().lower()
    for e in PromptStyle:
        if s == e.value:
            return e
    raise ValueError(f"Unknown style: {raw}, expected one of {[e.value for e in PromptStyle]}")
