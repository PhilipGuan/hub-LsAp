"""结构化输出模型：对应 OpenAI Agents 版的 output_type。"""
from pydantic import BaseModel, Field


class TableSelection(BaseModel):
    """第 2 步：选表结果。"""

    tables: list[str] = Field(description="需要用到的表名")
    reason: str = Field(description="选择理由")


class SQLPlan(BaseModel):
    """第 3 步：SQL 生成结果。"""

    sql: str = Field(description="只读 SELECT 语句")
    explanation: str = Field(description="SQL 思路说明")


class FinalAnswer(BaseModel):
    """Agent 版最终输出。"""

    answer: str = Field(description="中文结论")
    sql: str = Field(description="最终执行的 SQL")
    caveats: list[str] = Field(default_factory=list, description="注意事项/不确定性")