"""API 请求/响应模型。"""

from pydantic import BaseModel, Field

RISK_LEVELS = ("high", "medium", "low", "none", "not_corridor")

RISK_LABELS = {
    "high": "高风险",
    "medium": "中风险",
    "low": "低风险",
    "none": "无风险",
    "not_corridor": "非楼道",
}


class AnalyzeRequest(BaseModel):
    image: str = Field(
        ...,
        description="图片内容：data:image/...;base64, 开头的 Data URL，或纯 base64 字符串",
    )


class AnalyzeResponse(BaseModel):
    risk_level: str = Field(..., description="high | medium | low | none | not_corridor")
    risk_label: str = Field(..., description="中文风险等级名")
    confidence: float = Field(..., description="模型自评置信度 0~1")
    elements: list[str] = Field(..., description="识别到的关键元素")
    reason: str = Field(..., description="判断依据")
    suggestion: str = Field(..., description="处置建议")
    model: str = Field(..., description="使用的模型")
    elapsed_ms: int = Field(..., description="本次识别耗时（毫秒）")
