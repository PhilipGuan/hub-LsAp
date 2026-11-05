"""Agent 与编排层共用的 3 个输出结构。"""

from pydantic import BaseModel, Field


class TableSelection(BaseModel):
    tables: list[str] = Field(description="回答该问题所需的最少表名集合")
    reason: str = Field(default="", description="选择这些表的理由")
    needs_clarification: bool = Field(default=False, description="问题与数据库无关或信息不足")
    clarification_question: str | None = None


class SQLDraft(BaseModel):
    sql: str = Field(description="单条 SQLite SELECT 或 WITH ... SELECT 语句")
    explanation: str = Field(default="", description="SQL 如何回答用户问题")
    assumptions: list[str] = Field(default_factory=list)
    cannot_fix: bool = Field(default=False, description="改写时是否无法修复")


class FinalAnswer(BaseModel):
    answer: str = Field(description="面向用户的自然语言答案")
    evidence: list[str] = Field(default_factory=list, description="支撑答案的具体数值")
    caveats: list[str] = Field(default_factory=list)
