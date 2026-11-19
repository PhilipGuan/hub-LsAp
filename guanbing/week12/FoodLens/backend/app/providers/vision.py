"""视觉感知层：菜品识别 + 克数估计 + 营养标签 OCR。"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from ..config import Settings
from ..schemas import LocalizedText, NutritionPer100g
from .client import VisionError, chat_vision


class DetectedDish(BaseModel):
    name_zh: str = ""
    name_en: str = ""
    grams: float = 150.0
    confidence: float = 0.5


class Perception(BaseModel):
    items: list[DetectedDish] = Field(default_factory=list)
    label: NutritionPer100g | None = None
    warnings: list[LocalizedText] = Field(default_factory=list)


DISH_SYSTEM = """你是专业的食物图像分析助手。用户会给你一张一餐（可能有多道菜）的照片或文字描述。
请识别画面中每一道可辨认的菜品，并估计每道菜的可食重量（克）。
严格只输出 JSON，不要输出任何多余文字，格式：
{"items":[{"name_zh":"红烧肉","name_en":"Braised Pork Belly","grams":150,"confidence":0.9}]}
要求：
- name_zh 用简体中文，name_en 用英文。
- grams 为可食部分的重量估计（克）。
- confidence 为 0-1 的识别置信度。
- 只做识别与估重，不要输出热量或营养成分。
- 若无法识别任何菜品，返回 {"items":[]}。"""

LABEL_SYSTEM = """你是营养标签识别助手。用户会给你一张包装食品营养成分表的照片。
请读取其中的营养数据，并统一折算为「每 100 克」的数值。
严格只输出 JSON，不要输出任何多余文字，格式：
{"product_name":"","per_100g":{"kcal":0,"protein":0,"fat":0,"carb":0}}
要求：
- 若标签以千焦(kJ)标注能量，请换算为千卡(kcal)（1 kcal ≈ 4.184 kJ）。
- 若标签以「每份」标注，请按标签注明的每份克数折算为每 100 克。
- 无法读取时返回 {"per_100g":null}。"""


def _to_float(value: object, default: float) -> float:
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return result


def _demo(mode: str, text: str | None) -> Perception:
    if mode == "label":
        return Perception(
            label=NutritionPer100g(kcal=250, protein=11.0, fat=10.0, carb=28.0),
            warnings=[
                LocalizedText(
                    zh="Demo 模式：返回示例标签数值",
                    en="Demo mode: sample label values",
                )
            ],
        )

    names: list[str] = []
    if text:
        names = [part for part in re.split(r"[、,，/\s]+", text) if part]
    if not names:
        names = ["红烧肉", "白米饭", "清炒时蔬"]
    items = [
        DetectedDish(name_zh=name, name_en=name, grams=150.0, confidence=0.9)
        for name in names
    ]
    return Perception(
        items=items,
        warnings=[
            LocalizedText(
                zh="Demo 模式：结果基于文本输入或示例数据",
                en="Demo mode: results from text input or sample data",
            )
        ],
    )


async def _dishes(
    settings: Settings, *, image_bytes: bytes | None, text: str | None, mime: str
) -> Perception:
    user_prompt = "请识别这张图片中的每一道菜品并估计重量。"
    if text:
        user_prompt += f"\n补充信息：{text}"
    try:
        data = await chat_vision(
            settings,
            system_prompt=DISH_SYSTEM,
            user_prompt=user_prompt,
            image_bytes=image_bytes,
            mime=mime,
        )
    except VisionError as exc:
        return Perception(
            warnings=[
                LocalizedText(zh=f"图像识别失败：{exc}", en=f"Recognition failed: {exc}")
            ]
        )

    items: list[DetectedDish] = []
    for raw in data.get("items", []) or []:
        if not isinstance(raw, dict):
            continue
        items.append(
            DetectedDish(
                name_zh=str(raw.get("name_zh", "")).strip(),
                name_en=str(raw.get("name_en", "")).strip(),
                grams=_to_float(raw.get("grams"), 150.0),
                confidence=_to_float(raw.get("confidence"), 0.6),
            )
        )
    return Perception(items=items)


async def _label(
    settings: Settings, *, image_bytes: bytes | None, text: str | None, mime: str
) -> Perception:
    user_prompt = "请读取这张营养成分表，并折算为每 100 克的数值。"
    if text:
        user_prompt += f"\n补充信息：{text}"
    try:
        data = await chat_vision(
            settings,
            system_prompt=LABEL_SYSTEM,
            user_prompt=user_prompt,
            image_bytes=image_bytes,
            mime=mime,
        )
    except VisionError as exc:
        return Perception(
            warnings=[
                LocalizedText(zh=f"标签识别失败：{exc}", en=f"Label OCR failed: {exc}")
            ]
        )

    per = data.get("per_100g")
    if not isinstance(per, dict):
        return Perception(
            warnings=[
                LocalizedText(
                    zh="未能从标签读取到营养数据，请对准营养成分表重拍",
                    en="Could not read nutrition data; please retake the label photo",
                )
            ]
        )
    label = NutritionPer100g(
        kcal=_to_float(per.get("kcal"), 0.0),
        protein=_to_float(per.get("protein"), 0.0),
        fat=_to_float(per.get("fat"), 0.0),
        carb=_to_float(per.get("carb"), 0.0),
    )
    warnings: list[LocalizedText] = []
    product_name = str(data.get("product_name", "")).strip()
    if product_name:
        warnings.append(
            LocalizedText(zh=f"识别到产品：{product_name}", en=f"Detected product: {product_name}")
        )
    return Perception(label=label, warnings=warnings)


async def perceive(
    settings: Settings,
    *,
    mode: str,
    image_bytes: bytes | None,
    text: str | None,
    locale: str,
    mime: str = "image/jpeg",
) -> Perception:
    provider = settings.vision_provider.strip().lower()
    if provider == "demo":
        return _demo(mode, text)
    if mode == "label":
        return await _label(settings, image_bytes=image_bytes, text=text, mime=mime)
    return await _dishes(settings, image_bytes=image_bytes, text=text, mime=mime)
