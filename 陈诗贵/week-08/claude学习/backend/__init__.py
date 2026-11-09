"""深度研究助手后端包。

数据流：app（CLI / HTTP 入口）→ research（编排）→ engine（agentic 循环）
       → agent（DeepSeek LLM + 提示词）/ tools（Bocha 搜索）→ storage（落盘）。
"""
