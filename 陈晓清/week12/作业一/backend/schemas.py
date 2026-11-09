"""请求 / 响应数据结构"""
from typing import List, Optional

from pydantic import BaseModel, Field


class RiskData(BaseModel):
    risk_level: str = Field(description="high / medium / low / none / not_corridor / unknown")
    risk_name: str = Field(description="中文等级名")
    objects: List[str] = Field(default_factory=list, description="识别到的关键物品")
    reason: str = Field(default="", description="判定依据")
    suggestion: str = Field(default="", description="处置建议")
    elapsed_ms: int = Field(default=0, description="后端处理耗时")


class AnalyzeResponse(BaseModel):
    code: int = 0
    message: str = "ok"
    data: Optional[RiskData] = None
