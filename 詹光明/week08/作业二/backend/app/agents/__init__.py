"""各阶段的 LLM agent。

每个 agent 都是「纯函数 + 模板兜底」：输入 Pydantic 模型，输出 Pydantic 模型，
**任何 LLM 失败都降级到模板结果而不是抛出** —— 一次 LLM 抖动不该让整份研究挂掉。
"""
