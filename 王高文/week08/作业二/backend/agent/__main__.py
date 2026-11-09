"""包级自检：`python -m backend.agent` —— 验证导出齐全（不调用 LLM）。"""
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

from . import (  # noqa: E402
    BaseAgent,
    JudgeAgent,
    KeywordAgent,
    ReportAgent,
    SummaryAgent,
    parse_json,
)

if __name__ == "__main__":
    print("=== backend.agent 包导出自检 ===")
    for obj in (BaseAgent, KeywordAgent, SummaryAgent, JudgeAgent, ReportAgent):
        print(f"  {obj.__name__} 可导入，默认 model={obj.model}")
    assert callable(parse_json)
    # 无 key 时 should not raise（SDK 初始化已兜底）
    print("导出全部通过")
    print("可用 demo：python -m backend.agent.keyword / .summary / .judge / .report")