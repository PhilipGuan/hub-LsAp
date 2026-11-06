from __future__ import annotations

from pydantic import BaseModel, Field


class TableSelection(BaseModel):
    tables: list[str] = Field(
        description="回答用户问题必须使用的数据库表名列表",
    )
    reason: str = Field(
        default="",
        description="选择这些表的简短原因",
    )


class SQLDraft(BaseModel):
    sql: str = Field(
        description="一条只读的 SELECT 或 WITH SQL 语句",
    )
    explanation: str = Field(
        default="",
        description="SQL 的简短说明",
    )
