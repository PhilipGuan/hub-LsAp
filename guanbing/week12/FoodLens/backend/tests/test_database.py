"""营养库匹配测试。"""

from app.nutrition.database import get_database


def test_loads_seed():
    db = get_database()
    assert len(db) >= 30


def test_exact_zh_match():
    db = get_database()
    record = db.match("红烧肉")
    assert record is not None
    assert record.id == "hongshaorou"


def test_exact_en_match():
    db = get_database()
    record = db.match("White Rice")
    assert record is not None
    assert record.id == "white_rice"


def test_alias_match():
    db = get_database()
    record = db.match("西红柿炒鸡蛋")
    assert record is not None
    assert record.id == "tomato_egg"


def test_case_and_space_insensitive():
    db = get_database()
    record = db.match("  braised pork belly ")
    assert record is not None
    assert record.id == "hongshaorou"


def test_fuzzy_match():
    db = get_database()
    # 错别字容忍：宫爆鸡丁 -> 宫保鸡丁（别名）
    record = db.match("宫爆鸡")
    assert record is not None
    assert record.id == "kungpao_chicken"


def test_unknown_returns_none():
    db = get_database()
    assert db.match("量子纠缠炒饭") is None
    assert db.match("") is None
    assert db.match(None) is None


def test_get_by_id():
    db = get_database()
    assert db.get("pizza") is not None
    assert db.get("does-not-exist") is None
