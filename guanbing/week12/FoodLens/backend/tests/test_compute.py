"""营养计算与分量护栏测试。"""

from app.compute import (
    MAX_GRAMS,
    MIN_GRAMS,
    calibrate_grams,
    compute_totals,
    make_item,
    make_unmatched_item,
    nutrition_for,
)
from app.nutrition.database import get_database
from app.schemas import AnalyzedItem, LocalizedText


def _pork():
    record = get_database().get("hongshaorou")
    assert record is not None
    return record


def test_nutrition_for_scales_by_grams():
    record = _pork()  # 472 kcal / 100g
    kcal, protein, fat, carb = nutrition_for(record, 150)
    assert kcal == 708.0
    assert protein == 18.0
    assert fat == 67.5
    assert carb == 4.5


def test_calibrate_within_range_keeps_value():
    record = _pork()  # 标准份 150g -> 合理区间 [37.5, 600]
    grams, note = calibrate_grams(record, 200)
    assert grams == 200.0
    assert note is None


def test_calibrate_low_clamps_and_marks():
    record = _pork()
    grams, note = calibrate_grams(record, 10)
    assert grams == 37.5  # 0.25 * 150
    assert note is not None
    assert note.zh == "已校准"


def test_calibrate_high_clamps_and_marks():
    record = _pork()
    grams, note = calibrate_grams(record, 900)
    assert grams == 600.0  # 4.0 * 150
    assert note is not None
    assert note.en == "Calibrated"


def test_calibrate_absolute_bounds():
    grams, note = calibrate_grams(None, 99999)
    assert grams == MAX_GRAMS
    assert note is None
    grams, _ = calibrate_grams(None, -5)
    assert grams == MIN_GRAMS


def test_make_item_sets_matched_id():
    record = _pork()
    item = make_item(record=record, grams=150, confidence=0.9)
    assert item.matched_id == "hongshaorou"
    assert item.status == "matched"
    assert item.kcal == 708.0


def test_unmatched_item_excluded_from_totals():
    item = make_unmatched_item(
        name=LocalizedText(zh="未知", en="Unknown"), grams=100, confidence=0.5
    )
    assert item.status == "unmatched"
    assert item.kcal == 0.0
    totals = compute_totals([item])
    assert totals.kcal == 0.0


def test_compute_totals_sums_items():
    a = AnalyzedItem(
        name=LocalizedText(zh="a", en="a"),
        grams=100,
        kcal=100,
        protein=1,
        fat=2,
        carb=3,
    )
    b = AnalyzedItem(
        name=LocalizedText(zh="b", en="b"),
        grams=100,
        kcal=50.5,
        protein=4,
        fat=5,
        carb=6,
    )
    totals = compute_totals([a, b])
    assert totals.kcal == 150.5
    assert totals.protein == 5.0
    assert totals.fat == 7.0
    assert totals.carb == 9.0
