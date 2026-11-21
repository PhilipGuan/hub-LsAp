"""LangChain LLM 环境自检：一个带工具的简单 agent。

运行：python test_llm.py
"""

import sys

from langchain.agents import create_agent
from langchain_core.tools import tool

from shared.config import get_llm


@tool
def get_order_status(order_id: str) -> str:
    """查询订单状态。"""
    return {"A123": "已发货"}.get(order_id, "未找到")


def main() -> None:
    agent = create_agent(get_llm(temperature=0), tools=[get_order_status])
    result = agent.invoke({"messages": [{"role": "user", "content": "订单 A123 是什么状态？必须调用工具查询。"}]})
    for message in result["messages"]:
        print(f"{message.type}: {message.content}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
