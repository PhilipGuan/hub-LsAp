"""管线编排：感知 -> 归一/匹配 -> 计算。"""

from __future__ import annotations

from .compute import (
    calibrate_grams,
    compute_totals,
    make_item,
    make_label_item,
    make_unmatched_item,
)
from .config import Settings
from .nutrition.database import NutritionDatabase, get_database
from .providers import vision
from .schemas import AnalyzeResponse, AnalyzedItem, LocalizedText, RecomputeRequest


def _pick_name(zh: str | None, en: str | None) -> LocalizedText:
    zh = (zh or "").strip()
    en = (en or "").strip()
    if not zh and not en:
        return LocalizedText(zh="未识别菜品", en="Unrecognized dish")
    return LocalizedText(zh=zh or en, en=en or zh)


async def analyze(
    settings: Settings,
    *,
    mode: str,
    image_bytes: bytes | None,
    text: str | None,
    locale: str,
    mime: str = "image/jpeg",
    database: NutritionDatabase | None = None,
) -> AnalyzeResponse:
    db = database or get_database()
    perception = await vision.perceive(
        settings, mode=mode, image_bytes=image_bytes, text=text, locale=locale, mime=mime
    )
    warnings = list(perception.warnings)

    # 标签模式：直接使用标签数值（每 100g）
    if mode == "label" and perception.label is not None:
        item = make_label_item(
            name=LocalizedText(zh="包装食品", en="Packaged food"),
            per_100g=perception.label,
            note=LocalizedText(zh="来自营养标签（每 100g）", en="From nutrition label (per 100g)"),
        )
        return AnalyzeResponse(
            mode="label",
            items=[item],
            totals=compute_totals([item]),
            warnings=warnings,
        )

    items: list[AnalyzedItem] = []
    unmatched = 0
    for dish in perception.items:
        name = _pick_name(dish.name_zh, dish.name_en)
        record = db.match(dish.name_zh) or db.match(dish.name_en)
        if record is None:
            unmatched += 1
            items.append(
                make_unmatched_item(name=name, grams=dish.grams, confidence=dish.confidence)
            )
            continue
        grams, note = calibrate_grams(record, dish.grams)
        items.append(
            make_item(
                record=record, grams=grams, confidence=dish.confidence, note=note
            )
        )

    if unmatched:
        warnings.append(
            LocalizedText(
                zh=f"{unmatched} 道菜未收录，未计入总热量",
                en=f"{unmatched} dish(es) not in database, excluded from totals",
            )
        )
    if not items:
        warnings.append(
            LocalizedText(zh="未识别到任何菜品", en="No dishes recognized")
        )

    return AnalyzeResponse(
        mode="meal", items=items, totals=compute_totals(items), warnings=warnings
    )


def recompute(
    request: RecomputeRequest, database: NutritionDatabase | None = None
) -> AnalyzeResponse:
    """仅重算：不调用 VLM。命中库用库值，未命中标记未收录。"""
    db = database or get_database()
    items: list[AnalyzedItem] = []
    unmatched = 0

    for entry in request.items:
        record = None
        if entry.id:
            record = db.get(entry.id)
        if record is None and (entry.name_zh or entry.name_en):
            record = db.match(entry.name_zh) or db.match(entry.name_en)

        if record is None:
            unmatched += 1
            items.append(
                make_unmatched_item(
                    name=_pick_name(entry.name_zh, entry.name_en),
                    grams=entry.grams,
                    confidence=1.0,
                )
            )
            continue

        grams, note = calibrate_grams(record, entry.grams)
        items.append(
            make_item(record=record, grams=grams, confidence=1.0, note=note)
        )

    warnings: list[LocalizedText] = []
    if unmatched:
        warnings.append(
            LocalizedText(
                zh=f"{unmatched} 道菜未收录，未计入总热量",
                en=f"{unmatched} dish(es) not in database, excluded from totals",
            )
        )
    return AnalyzeResponse(
        mode="meal", items=items, totals=compute_totals(items), warnings=warnings
    )
