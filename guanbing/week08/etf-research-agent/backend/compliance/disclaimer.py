# -*- coding: utf-8 -*-
"""M4 B2 合规：Disclaimer + B3 入口拦截（Dry-Run 骨架）。"""
from __future__ import annotations

from ..models import Disclaimer


DEFAULT_DISCLAIMER_STATEMENTS = [
    "本报告由 AI 自动生成，不构成任何投资建议或邀约。",
    "历史业绩不代表未来表现，投资有风险，入市需谨慎。",
    "数据来源于公开第三方渠道，不保证其及时性与准确性。",
    "所有决策请咨询持牌金融顾问，本工具不对任何损失承担责任。",
]


class DisclaimerRenderer:
    """生成三位置免责声明注入文本（B1 合规）。"""

    def __init__(self, version: str = "v1.0") -> None:
        self.disclaimer = Disclaimer(
            version=version,
            statements=DEFAULT_DISCLAIMER_STATEMENTS.copy(),
        )

    def render_markdown(
        self, where: str = "top"
    ) -> str:
        """where ∈ {top, before_conclusion, footer} 三位置。"""
        raise NotImplementedError

    def render_html(self, where: str = "top") -> str:
        raise NotImplementedError


class ComplianceGate:
    """B3 合规入口拦截（CLI --disclaimer-agree / API JSON disclaimer_agree）。"""

    ERROR_EXIT_CODE = 2
    ERROR_HTTP_CODE = 400
    ERROR_MSG_CN = "合规 B3：必须显式同意免责声明后才能执行研究任务。"

    def cli_agree_ok(self, disclaimer_agree_flag: bool) -> tuple[bool, int, str]:
        """返回 (ok, exit_code, reason_msg)。ok=False 时 CLI 直接 exit。"""
        if disclaimer_agree_flag:
            return True, 0, ""
        return False, self.ERROR_EXIT_CODE, self.ERROR_MSG_CN

    def api_agree_ok(self, payload: dict) -> tuple[bool, int, str]:
        ok = bool(payload.get("disclaimer_agree") is True)
        if ok:
            return True, 0, ""
        return False, self.ERROR_HTTP_CODE, self.ERROR_MSG_CN
