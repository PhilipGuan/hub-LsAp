"""营养计算与分量护栏。"""

from __future__ import annotations

from .schemas import (
    AnalyzedItem,
    FoodRecord,
    LocalizedText,
    NutritionPer100g,
    Totals,
)

MIN_GRAMS = 5.0
MAX_GRAMS = 1500.0
# 护栏：估算克数偏离库中标准份的倍数区间
LOWER_FACTOR = 0.25
UPPER_FACTOR = 4.0


def calibrate_grams(
    record: FoodRecord | None, grams: float
) -> tuple[float, LocalizedText | None]:
    """限制克数范围；明显偏离库中标准份时向合理区间收敛并标注「已校准」。"""
    grams = max(MIN_GRAMS, min(MAX_GRAMS, float(grams)))
    if record is None or not record.standard_servings:
        return round(grams, 1), None
    base = record.standard_servings[0].grams
    low, high = base * LOWER_FACTOR, base * UPPER_FACTOR
    if grams < low:
        return round(low, 1), LocalizedText(zh="已校准", en="Calibrated")
    if grams > high:
        return round(high, 1), LocalizedText(zh="已校准", en="Calibrated")
    return round(grams, 1), None


def nutrition_for(
    record: FoodRecord, grams: float
) -> tuple[float, float, float, float]:
    factor = grams / 100.0
    per = record.per_100g
    return (
        round(per.kcal * factor, 1),
        round(per.protein * factor, 1),
        round(per.fat * factor, 1),
        round(per.carb * factor, 1),
    )


def make_item(
    *,
    record: FoodRecord,
    grams: float,
    confidence: float,
    status: str = "matched",
    note: LocalizedText | None = None,
) -> AnalyzedItem:
    kcal, protein, fat, carb = nutrition_for(record, grams)
    return AnalyzedItem(
        name=record.name,
        matched_id=record.id,
        grams=grams,
        kcal=kcal,
        protein=protein,
        fat=fat,
        carb=carb,
        confidence=confidence,
        status=status,  # type: ignore[arg-type]
        note=note,
        source=record.source,
    )


def make_label_item(
    *,
    name: LocalizedText,
    per_100g: NutritionPer100g,
    grams: float = 100.0,
    note: LocalizedText | None = None,
) -> AnalyzedItem:
    factor = grams / 100.0
    return AnalyzedItem(
        name=name,
        matched_id=None,
        grams=grams,
        kcal=round(per_100g.kcal * factor, 1),
        protein=round(per_100g.protein * factor, 1),
        fat=round(per_100g.fat * factor, 1),
        carb=round(per_100g.carb * factor, 1),
        confidence=1.0,
        status="matched",
        note=note,
    )


def make_unmatched_item(
    *, name: LocalizedText, grams: float, confidence: float
) -> AnalyzedItem:
    return AnalyzedItem(
        name=name,
        matched_id=None,
        grams=round(float(grams), 1),
        kcal=0.0,
        protein=0.0,
        fat=0.0,
        carb=0.0,
        confidence=confidence,
        status="unmatched",
        note=LocalizedText(zh="未收录，未计入热量", en="Not in database; excluded"),
    )


def compute_totals(items: list[AnalyzedItem]) -> Totals:
    return Totals(
        kcal=round(sum(item.kcal for item in items), 1),
        protein=round(sum(item.protein for item in items), 1),
        fat=round(sum(item.fat for item in items), 1),
        carb=round(sum(item.carb for item in items), 1),
    )
