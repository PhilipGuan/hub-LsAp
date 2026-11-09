"""3 个输出结构，Agent 与编排层共用。"""

from pydantic import BaseModel, Field


class TableSelection(BaseModel):
    """选表 Agent 的输出。"""
    # ge=0：greater than or equal，大于等于 0（>= 0）
    # le=1：less than or equal，小于等于 1（<= 1）
    tables: list[str] = Field(description="回答该问题所需的最少表名集合")
    reason: str = Field(description="选择这些表的理由")
    confidence: float = Field(default=0.0, ge=0, le=1)
    needs_clarification: bool = Field(default=False, description="问题与数据库无关或信息不足")
    clarification_question: str | None = None


class SQLDraft(BaseModel):
    """生成 / 改写 Agent 的输出。"""
    # default_factory 是给字段指定一个工厂函数，每次创建实例时调用它一次，用返回值作为默认值。default_factory=list 就是每次调一次 list()，得到一个全新的空列表。
    sql: str = Field(description="单条 SQLite SELECT 或 WITH ... SELECT 语句")
    explanation: str = Field(default="", description="SQL 如何回答用户问题")
    assumptions: list[str] = Field(default_factory=list)
    cannot_fix: bool = Field(default=False, description="改写时是否无法修复")


class FinalAnswer(BaseModel):
    """汇总 Agent 的输出。"""

    answer: str = Field(description="面向用户的自然语言答案")
    evidence: list[str] = Field(default_factory=list, description="支撑答案的具体数值")
    caveats: list[str] = Field(default_factory=list)
