# -*- coding: utf-8 -*-
"""agent 包级自检 demo（python -m backend.agent 触发）。

只做**纯本地**导出自检：确认 4 个角色 agent 能正常导入、实例化，
且各自的提示词模板能渲染出非空文本。**不发网络请求 / 不调 LLM**——
需要真跑 LLM 请用 `python -m backend.agent.keyword` 等模块级 demo。
"""

from __future__ import annotations

import logging

from .. import config
from . import BaseAgent, JudgeAgent, KeywordAgent, ReportAgent, SummaryAgent
from . import __all__ as AGENT_EXPORTS

logger = logging.getLogger(__name__)

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    agents = [KeywordAgent(), SummaryAgent(), JudgeAgent(), ReportAgent()]
    render_vars = {
        "topic": "测试主题",
        "keyword": "测试关键词",
        "today": config.today_str(),
    }

    print("=== 角色 agent 导出自检 ===")
    for agent in agents:
        assert isinstance(agent, BaseAgent), f"{type(agent).__name__} 应继承 BaseAgent"
        assert agent.template_name, f"{agent.agent_name} 未指定 template_name"
        prompt = agent._render(**render_vars)
        assert prompt.strip(), f"{agent.agent_name} 的提示词模板渲染为空"
        print(
            f"  {agent.agent_name:<14} model={agent.model:<18} "
            f"template={agent.template_name:<26} 提示词={len(prompt)} 字符"
        )

    assert [a.agent_name for a in agents] == [
        "KeywordAgent",
        "SummaryAgent",
        "JudgeAgent",
        "ReportAgent",
    ], "agent_name 与预期不符"
    assert len(AGENT_EXPORTS) == 5, f"__all__ 应导出 5 个名字，实际 {AGENT_EXPORTS}"

    # 各 agent 的公开方法都要挂上
    for agent, method in zip(
        agents, ["generate_keywords", "summarize", "judge", "generate"]
    ):
        assert callable(getattr(agent, method)), f"{agent.agent_name} 缺少方法 {method}"
        print(f"  {agent.agent_name}.{method} 就绪")

    print(f"BASE_MODEL   = {config.MODEL_NAME}")
    print(f"TEMPLATE_DIR = {config.TEMPLATE_DIR}")
    print("agent 包导出自检 OK")
