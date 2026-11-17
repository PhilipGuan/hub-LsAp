"""两实现共用的数据契约（设计文档 §3、§4.1）。

进程1 输出 = SentimentResult；进程2 输出 = reply；
后端返回前端 = PipelineResult；前端请求 = ChatRequest。
"""

from typing import Literal

from pydantic import BaseModel, Field, field_validator

# 情感标签（枚举，供结构化输出与路由使用）
SentimentLabel = Literal["positive", "negative", "neutral"]


class SentimentResult(BaseModel):
    """进程1 的输出 = 进程2 的输入。"""

    label: SentimentLabel  # 情感标签
    confidence: float = Field(ge=0, le=1)  # 置信度 0~1
    reason: str  # 一句话判断依据


class PipelineResult(BaseModel):
    """后端返回给前端的完整结果。"""

    user_input: str
    sentiment: SentimentResult  # 进程1 结果
    reply: str  # 进程2 生成的回复
    branch: str  # 命中的分支: encourage / comfort / neutral_chat
    engine: str  # "langgraph" | "openai-agents"
    elapsed_ms: int  # 端到端耗时


class ChatRequest(BaseModel):
    """前端请求体：非空且不超过 500 字，否则 422（设计文档 §7）。"""

    text: str = Field(min_length=1, max_length=500, description="用户输入")

    @field_validator("text")
    @classmethod
    def strip_and_check(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("输入不能为空")
        return v
