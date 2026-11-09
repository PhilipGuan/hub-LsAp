"""请求 / 响应数据模型。"""
from typing import Literal

from pydantic import BaseModel, Field

# 五档风险等级
RiskLevel = Literal["无", "低", "中", "高", "非楼道"]


class RiskAssessment(BaseModel):
    """一次风险评估的结构化结果。"""

    is_corridor: bool = Field(description="图片是否拍摄的是楼道/楼梯间")
    risk_level: RiskLevel = Field(description="风险等级")
    risk_items: list[str] = Field(
        default_factory=list,
        description="命中的风险因素，例如 ['电动车', '飞线充电']",
    )
    reason: str = Field(description="判定依据")
    suggestion: str = Field(description="处置建议")
    confidence: float = Field(ge=0, le=1, description="置信度 0~1")


class BasicResponse(BaseModel):
    """统一响应外壳。"""

    code: int
    message: str
    data: RiskAssessment | None = None