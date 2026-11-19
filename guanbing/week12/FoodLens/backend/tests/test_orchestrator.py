"""编排管线测试（demo provider，不调用网络）。"""

import asyncio

from app.config import Settings
from app.orchestrator import analyze, recompute
from app.schemas import RecomputeItem, RecomputeRequest


def _demo_settings() -> Settings:
    return Settings(vision_provider="demo", llm_api_key="", llm_base_url="")


def test_analyze_meal_matches_known_dishes():
    response = asyncio.run(
        analyze(
            _demo_settings(),
            mode="meal",
            image_bytes=None,
            text="红烧肉、白米饭、清炒时蔬",
            locale="zh-CN",
        )
    )
    assert response.mode == "meal"
    assert len(response.items) == 3
    assert all(item.status == "matched" for item in response.items)
    assert response.totals.kcal > 0


def test_analyze_meal_unknown_dish_warns():
    response = asyncio.run(
        analyze(
            _demo_settings(),
            mode="meal",
            image_bytes=None,
            text="量子纠缠炒饭",
            locale="zh-CN",
        )
    )
    assert response.items[0].status == "unmatched"
    assert response.warnings
    assert response.totals.kcal == 0.0


def test_analyze_label_mode_returns_per_100g():
    response = asyncio.run(
        analyze(
            _demo_settings(),
            mode="label",
            image_bytes=None,
            text=None,
            locale="zh-CN",
        )
    )
    assert response.mode == "label"
    assert len(response.items) == 1
    assert response.items[0].grams == 100.0
    assert response.items[0].kcal == 250.0


def test_recompute_by_id():
    response = recompute(
        RecomputeRequest(
            locale="zh-CN",
            items=[RecomputeItem(id="hongshaorou", grams=300)],
        )
    )
    assert response.items[0].kcal == 1416.0  # 472 * 3
    assert response.totals.kcal == 1416.0


def test_recompute_by_name_and_unmatched():
    response = recompute(
        RecomputeRequest(
            locale="zh-CN",
            items=[
                RecomputeItem(name_zh="宫保鸡丁", grams=100),
                RecomputeItem(name_zh="不存在的菜", grams=120),
            ],
        )
    )
    assert response.items[0].matched_id == "kungpao_chicken"
    assert response.items[1].status == "unmatched"
    assert response.warnings
    assert response.totals.kcal == 165.0
