from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    risk_level: Literal["高风险", "中风险", "低风险", "无风险", "非楼道"]
    reason: str = Field(min_length=1, max_length=1000)
