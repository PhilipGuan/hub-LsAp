"""请求 / 响应数据模型。"""

from typing import Literal, Optional

from pydantic import BaseModel, Field


class LocalizedText(BaseModel):
    zh: str
    en: str


class NutritionPer100g(BaseModel):
    kcal: float
    protein: float
    fat: float
    carb: float


class StandardServing(BaseModel):
    label: LocalizedText
    grams: float


class FoodRecord(BaseModel):
    """营养库中的一条食物记录。"""

    id: str
    name: LocalizedText
    aliases: list[str] = Field(default_factory=list)
    category: str = "dish"
    per_100g: NutritionPer100g
    standard_servings: list[StandardServing] = Field(default_factory=list)
    # 数据来源：seed（手工菜肴） | manual | usda | china_fct
    source: str = "seed"
    # USDA FoodData Central 溯源 id（仅 usda 来源有值）
    fdc_id: Optional[str] = None


ItemStatus = Literal["matched", "unmatched", "estimated"]


class AnalyzedItem(BaseModel):
    name: LocalizedText
    matched_id: Optional[str] = None
    grams: float
    kcal: float
    protein: float
    fat: float
    carb: float
    confidence: float = 0.0
    status: ItemStatus = "matched"
    note: Optional[LocalizedText] = None
    # 命中的库条目来源，供前端展示「数据来源」
    source: Optional[str] = None


class Totals(BaseModel):
    kcal: float = 0.0
    protein: float = 0.0
    fat: float = 0.0
    carb: float = 0.0


class AnalyzeResponse(BaseModel):
    mode: Literal["meal", "label"] = "meal"
    items: list[AnalyzedItem] = Field(default_factory=list)
    totals: Totals = Field(default_factory=Totals)
    warnings: list[LocalizedText] = Field(default_factory=list)


class RecomputeItem(BaseModel):
    id: Optional[str] = None
    name_zh: Optional[str] = None
    name_en: Optional[str] = None
    grams: float


class RecomputeRequest(BaseModel):
    locale: str = "zh-CN"
    items: list[RecomputeItem] = Field(default_factory=list)
