#!/usr/bin/env python
"""离线摄入 USDA FoodData Central 天然食物营养数据。

设计依据：docs/superpowers/specs/2026-11-19-foodlens-nutrition-data-design.md

用法::

    uv run python scripts/ingest_usda.py                  # 拉取并写入 natural_usda.json
    uv run python scripts/ingest_usda.py --check          # 只比对差异，不写盘
    uv run python scripts/ingest_usda.py --recompute-meta # 仅重建 _meta.json
    uv run python scripts/ingest_usda.py --refresh        # 忽略本地缓存，重新拉取

API Key 读取顺序：命令行 --api-key > 环境变量 USDA_API_KEY > .env 同名键 > DEMO_KEY。
DEMO_KEY 限流 30 次/小时，仅够试跑；正式建库请到 data.gov 免费申请。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, NamedTuple

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.nutrition.database import _normalize  # noqa: E402  （复用同一套归一逻辑做冲突检测）
from app.schemas import FoodRecord  # noqa: E402

SEARCH_URL = "https://api.nal.usda.gov/fdc/v1/foods/search"
DATA_TYPES = "Foundation,SR Legacy"

DATA_DIR = BACKEND_DIR / "app" / "nutrition" / "data"
OUT_PATH = DATA_DIR / "natural_usda.json"
META_PATH = DATA_DIR / "_meta.json"
SEED_PATH = DATA_DIR / "seed.json"
CACHE_DIR = BACKEND_DIR / "data" / "usda"

# USDA nutrientId
NID_ENERGY_KCAL = 2047  # Foundation: Energy (Atwater General), unitName=KCAL
NID_ENERGY_KCAL_2 = 2048  # Foundation: Energy (Atwater Specific), unitName=KCAL
NID_ENERGY = 1008  # 经典能量字段（kcal 或 kJ）
NID_ENERGY_KJ = 1062  # SR Legacy: Energy, unitName=kJ
NID_PROTEIN = 1003
NID_FAT = 1004
NID_CARB = 1005

KJ_PER_KCAL = 4.184
REQUEST_INTERVAL_SECONDS = 1.1  # ≥1s，避免触碰 1000/h 限流
PAGE_SIZE = 5

# USDA 描述里常见的「加工方式 / 部位 / 状态 / 规格」修饰词。
# 用于区分「同名食物的合理变体」与「同科异种」（后者含未知词，会被过滤掉）。
QUALIFIERS = {
    # 加工 / 烹调
    "cooked", "boiled", "drained", "roasted", "roast", "baked", "broiled", "grilled",
    "fried", "steamed", "heated", "prepared", "unprepared", "refrigerated", "frozen",
    "canned", "dried", "dry", "dehydrated", "raw", "uncooked", "chopped", "sliced",
    "diced", "ground", "whole", "halves", "half", "pieces", "mashed", "puree",
    "solids", "solid", "liquid", "syrup", "pack", "packed", "juice", "concentrate",
    "ready", "to", "eat",
    # 部位 / 形态
    "meat", "skin", "skins", "only", "separable", "lean", "fat", "boneless", "bone",
    "in", "loin", "top", "center", "chop", "chops", "roasts", "rib", "ribs", "shoulder",
    "leg", "breast", "thigh", "wing", "back", "neck", "tenderloin", "sirloin", "round",
    "flank", "brisket", "gizzard", "heart", "kidney", "liver", "feet", "flesh",
    "fillets", "fillet", "steak", "cutlet", "nuggets", "roll", "sliced", "ground",
    "broilers", "fryers", "hen", "hens", "capons", "stewing", "roasting", "fresh",
    "young", "domestic", "imported", "wild", "farmed", "atlantic", "chinook",
    "pink", "coho", "sockeye", "yellowfin", "skipjack", "light", "chunk", "flaked",
    # 蛋奶 / 油脂
    "light", "dark", "heavy", "whipping", "cream", "regular", "plain", "sweet",
    "salted", "unsalted", "salt", "added", "vitamin", "nk", "d", "a",
    "lowfat", "nonfat", "reduced", "free", "skim", "skimmed", "part", "partially",
    "soy", "olive", "vegetable", "peanut", "canola", "corn", "sunflower", "safflower",
    "salad", "cooking", "virgin", "extra", "refined", "unrefined", "cold", "pressed",
    "hydrogenated", "deodorized", "mechanically", "expeller",
    # 颜色 / 成熟度 / 品种后缀（多为果蔬）
    "green", "red", "yellow", "white", "brown", "black", "orange", "purple",
    "ripe", "unripe", "native", "english", "sweet", "sour", "mature", "immature",
    "all", "types", "type", "seeds", "seed", "kernels", "kernel", "sprouts", "leaves",
    "leafy", "tips", "pods", "edible", "portion", "refuse", "waste", "includes",
    "calcium", "sulfate", "magnesium", "chloride", "nigari", "firm", "soft", "silken",
    "flour", "grain", "grains", "enriched", "unenriched", "fortified", "bleached",
    "unbleached", "purpose", "granulated", "confectioners", "raw", "from", "with",
    "without", "and", "or", "of", "ns", "as", "purchased", "method", "heat", "moist",
    "grade", "a", "b", "c", "i", "ii",
}


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


class FoodSpec(NamedTuple):
    """一条待摄入食物的元数据（营养数值由 USDA 提供，此处只给名称与份量）。"""

    query: str  # USDA 英文检索词
    id: str  # 生成的记录 id（统一 usda_ 前缀，与手工 seed 条目隔离）
    zh: str
    en: str
    category: str
    aliases: tuple[str, ...] = ()
    serving_grams: float = 100.0
    serving_zh: str = "100克"
    serving_en: str = "100 g"


def S(**kwargs: Any) -> FoodSpec:
    return FoodSpec(**kwargs)


# fmt: off
# 高频天然食物映射表（L1 层）。营养数值一律来自 USDA，逐条可溯源 fdc_id。
# 注意：苹果 / 香蕉 / 鸡蛋 / 牛奶 / 白米饭等已由手工 seed.json 覆盖，此处不重复。
USDA_FOODS: list[FoodSpec] = [
    # ---------- 主食 / 谷物 ----------
    S(query="rice brown long-grain cooked", id="usda_brown_rice", zh="糙米饭", en="Brown Rice", category="staple",
      aliases=("糙米", "Brown Rice, cooked"), serving_grams=150, serving_zh="一碗(约150g)", serving_en="1 bowl (~150g)"),
    S(query="rice glutinous cooked", id="usda_glutinous_rice", zh="糯米", en="Glutinous Rice", category="staple",
      aliases=("江米", "Sticky Rice")),
    S(query="millet cooked", id="usda_millet", zh="小米", en="Millet", category="staple", aliases=("小米粥", "Millet, cooked")),
    S(query="oats", id="usda_oats", zh="燕麦", en="Oats", category="staple", aliases=("燕麦片", "Rolled Oats", "Oatmeal")),
    S(query="quinoa cooked", id="usda_quinoa", zh="藜麦", en="Quinoa", category="staple"),
    S(query="buckwheat groats roasted cooked", id="usda_buckwheat", zh="荞麦", en="Buckwheat", category="staple",
      aliases=("荞麦面", "Kasha")),
    S(query="bread whole-wheat", id="usda_whole_wheat_bread", zh="全麦面包", en="Whole-wheat Bread", category="staple",
      aliases=("全麦吐司", "Whole Wheat Bread"), serving_grams=60, serving_zh="一片(约60g)", serving_en="1 slice (~60g)"),
    S(query="bread white commercially prepared", id="usda_white_bread", zh="白面包", en="White Bread", category="staple",
      aliases=("吐司", "Toast", "White Bread"), serving_grams=60, serving_zh="一片(约60g)", serving_en="1 slice (~60g)"),
    S(query="corn sweet yellow cooked", id="usda_corn", zh="玉米", en="Sweet Corn", category="staple",
      aliases=("甜玉米", "玉米棒", "Corn"), serving_grams=150, serving_zh="一根(约150g)", serving_en="1 ear (~150g)"),
    S(query="sweet potato baked", id="usda_sweet_potato", zh="红薯", en="Sweet Potato", category="staple",
      aliases=("地瓜", "番薯", "甘薯"), serving_grams=150, serving_zh="一个(约150g)", serving_en="1 piece (~150g)"),
    S(query="potato baked flesh and skin", id="usda_potato", zh="土豆", en="Potato", category="staple",
      aliases=("马铃薯", "洋芋", "Potato, baked"), serving_grams=170, serving_zh="一个(约170g)", serving_en="1 potato (~170g)"),
    S(query="taro cooked", id="usda_taro", zh="芋头", en="Taro", category="staple", aliases=("芋艿",)),
    S(query="yam cooked", id="usda_yam", zh="山药", en="Yam", category="staple", aliases=("淮山", "山药片")),
    S(query="flour wheat white", id="usda_wheat_flour", zh="面粉", en="Wheat Flour", category="staple",
      aliases=("小麦粉", "面粉(小麦)", "Flour")),
    S(query="wheat flour whole-grain", id="usda_whole_wheat_flour", zh="全麦面粉", en="Whole-wheat Flour", category="staple",
      aliases=("全麦粉",)),

    # ---------- 肉类 ----------
    S(query="chicken breast roasted", id="usda_chicken_breast", zh="鸡胸肉", en="Chicken Breast", category="meat",
      aliases=("鸡胸", "Chicken Breast, roasted"), serving_grams=120, serving_zh="一块(约120g)", serving_en="1 piece (~120g)"),
    S(query="chicken thigh cooked", id="usda_chicken_thigh", zh="鸡腿肉", en="Chicken Thigh", category="meat",
      aliases=("鸡腿",), serving_grams=100, serving_zh="一个(约100g)", serving_en="1 piece (~100g)"),
    S(query="chicken wing cooked", id="usda_chicken_wing", zh="鸡翅", en="Chicken Wing", category="meat",
      aliases=("鸡翅膀",), serving_grams=60, serving_zh="一个(约60g)", serving_en="1 piece (~60g)"),
    S(query="chicken roasting meat and skin cooked", id="usda_chicken", zh="鸡肉", en="Chicken", category="meat",
      aliases=("鸡肉(带皮)", "Roast Chicken")),
    S(query="pork loin cooked", id="usda_pork_lean", zh="瘦猪肉", en="Lean Pork", category="meat",
      aliases=("猪里脊", "猪肉", "Pork Loin")),
    S(query="pork fresh belly raw", id="usda_pork_belly", zh="五花肉", en="Pork Belly", category="meat",
      aliases=("猪五花", "Pork Belly")),
    S(query="pork fresh spareribs cooked", id="usda_pork_ribs", zh="猪排骨", en="Pork Ribs", category="meat",
      aliases=("排骨", "肋排", "Spare Ribs")),
    S(query="beef lean cooked", id="usda_beef_lean", zh="瘦牛肉", en="Lean Beef", category="meat",
      aliases=("牛肉", "Beef"), serving_grams=100, serving_zh="100克", serving_en="100 g"),
    S(query="beef brisket cooked", id="usda_beef_brisket", zh="牛腩", en="Beef Brisket", category="meat",
      aliases=("牛腩肉", "Brisket")),
    S(query="ground beef cooked", id="usda_ground_beef", zh="牛肉末", en="Ground Beef", category="meat",
      aliases=("牛肉碎", "牛绞肉", "Minced Beef")),
    S(query="lamb domestic cooked", id="usda_lamb", zh="羊肉", en="Lamb", category="meat", aliases=("羊肉(熟)", "Lamb, cooked")),
    S(query="duck roasted meat and skin", id="usda_duck", zh="鸭肉", en="Duck", category="meat", aliases=("烤鸭肉", "Duck, roasted")),
    S(query="bacon cooked", id="usda_bacon", zh="培根", en="Bacon", category="meat", aliases=("烟肉",),
      serving_grams=30, serving_zh="一片(约30g)", serving_en="1 slice (~30g)"),
    S(query="ham sliced", id="usda_ham", zh="火腿", en="Ham", category="meat", aliases=("火腿片", "Ham, sliced")),
    S(query="sausage pork cooked", id="usda_sausage", zh="香肠", en="Sausage", category="meat",
      aliases=("腊肠", "Pork Sausage"), serving_grams=50, serving_zh="一根(约50g)", serving_en="1 link (~50g)"),
    S(query="pork liver cooked", id="usda_pork_liver", zh="猪肝", en="Pork Liver", category="meat", aliases=("猪肝(熟)",)),
    S(query="chicken liver cooked", id="usda_chicken_liver", zh="鸡肝", en="Chicken Liver", category="meat"),
    S(query="turkey breast roasted", id="usda_turkey_breast", zh="火鸡胸肉", en="Turkey Breast", category="meat"),

    # ---------- 水产 ----------
    S(query="salmon cooked dry heat", id="usda_salmon", zh="三文鱼", en="Salmon", category="seafood",
      aliases=("大马哈鱼", "Salmon, cooked"), serving_grams=120, serving_zh="一块(约120g)", serving_en="1 fillet (~120g)"),
    S(query="tuna light canned in water", id="usda_tuna", zh="金枪鱼", en="Tuna", category="seafood",
      aliases=("吞拿鱼", "Tuna, canned")),
    S(query="shrimp cooked moist heat", id="usda_shrimp", zh="虾", en="Shrimp", category="seafood",
      aliases=("大虾", "对虾", "基围虾", "Prawn")),
    S(query="crab cooked moist heat", id="usda_crab", zh="蟹", en="Crab", category="seafood", aliases=("螃蟹", "蟹肉")),
    S(query="cod pacific cooked", id="usda_cod", zh="鳕鱼", en="Cod", category="seafood", aliases=("银鳕鱼",)),
    S(query="tilapia cooked", id="usda_tilapia", zh="罗非鱼", en="Tilapia", category="seafood", aliases=("非洲鲫鱼",)),
    S(query="oyster eastern cooked", id="usda_oyster", zh="生蚝", en="Oyster", category="seafood", aliases=("牡蛎",)),
    S(query="squid cooked", id="usda_squid", zh="鱿鱼", en="Squid", category="seafood", aliases=("乌贼",)),
    S(query="clam cooked", id="usda_clam", zh="蛤蜊", en="Clam", category="seafood", aliases=("花蛤",)),
    S(query="scallop cooked", id="usda_scallop", zh="扇贝", en="Scallop", category="seafood", aliases=("扇贝肉",)),
    S(query="sardine canned in oil", id="usda_sardine", zh="沙丁鱼", en="Sardine", category="seafood"),
    S(query="mackerel cooked", id="usda_mackerel", zh="鲭鱼", en="Mackerel", category="seafood", aliases=("青鱼", "鲐鱼")),

    # ---------- 蛋 ----------
    S(query="egg white raw", id="usda_egg_white", zh="蛋白", en="Egg White", category="egg",
      aliases=("蛋清", "Egg White, raw")),
    S(query="egg yolk raw", id="usda_egg_yolk", zh="蛋黄", en="Egg Yolk", category="egg", aliases=("Egg Yolk, raw")),

    # ---------- 奶制品 ----------
    S(query="cheese cheddar", id="usda_cheese", zh="奶酪", en="Cheese", category="dairy",
      aliases=("芝士", "切达奶酪", "Cheddar"), serving_grams=30, serving_zh="一片(约30g)", serving_en="1 slice (~30g)"),
    S(query="butter salted", id="usda_butter", zh="黄油", en="Butter", category="dairy", aliases=("牛油",),
      serving_grams=10, serving_zh="一勺(约10g)", serving_en="1 tsp (~10g)"),
    S(query="milk nonfat", id="usda_skim_milk", zh="脱脂牛奶", en="Skim Milk", category="dairy",
      aliases=("脱脂奶", "Skimmed Milk"), serving_grams=250, serving_zh="一杯(约250g)", serving_en="1 cup (~250g)"),
    S(query="cream heavy whipping", id="usda_cream", zh="淡奶油", en="Heavy Cream", category="dairy",
      aliases=("鲜奶油", "掼奶油")),

    # ---------- 豆制品 / 豆类 ----------
    S(query="tofu firm prepared with calcium sulfate", id="usda_tofu", zh="豆腐", en="Tofu", category="soy",
      aliases=("老豆腐", "北豆腐", "Bean Curd"), serving_grams=100, serving_zh="一块(约100g)", serving_en="1 block (~100g)"),
    S(query="soybeans mature seeds cooked", id="usda_soybean", zh="黄豆", en="Soybean", category="soy", aliases=("大豆",)),
    S(query="edamame frozen cooked", id="usda_edamame", zh="毛豆", en="Edamame", category="soy", aliases=("青豆",)),
    S(query="beans kidney all types cooked", id="usda_kidney_bean", zh="芸豆", en="Kidney Bean", category="bean",
      aliases=("腰豆",)),
    S(query="beans black mature seeds cooked", id="usda_black_bean", zh="黑豆", en="Black Bean", category="bean"),
    S(query="beans pinto cooked", id="usda_pinto_bean", zh="斑豆", en="Pinto Bean", category="bean"),
    S(query="chickpeas cooked", id="usda_chickpea", zh="鹰嘴豆", en="Chickpea", category="bean", aliases=("鸡豆",)),
    S(query="lentils cooked", id="usda_lentil", zh="小扁豆", en="Lentil", category="bean", aliases=("兵豆",)),
    S(query="beans snap green cooked", id="usda_green_bean", zh="四季豆", en="Green Bean", category="vegetable",
      aliases=("刀豆", "青豆角", "豆角")),
    S(query="peas green cooked", id="usda_peas", zh="豌豆", en="Green Peas", category="vegetable", aliases=("青豌豆",)),

    # ---------- 蔬菜 ----------
    S(query="spinach raw", id="usda_spinach", zh="菠菜", en="Spinach", category="vegetable"),
    S(query="broccoli cooked", id="usda_broccoli", zh="西兰花", en="Broccoli", category="vegetable",
      aliases=("绿花椰菜", "青花菜")),
    S(query="cauliflower cooked", id="usda_cauliflower", zh="花椰菜", en="Cauliflower", category="vegetable",
      aliases=("菜花", "花菜")),
    S(query="cabbage raw", id="usda_cabbage", zh="卷心菜", en="Cabbage", category="vegetable",
      aliases=("圆白菜", "包菜", "甘蓝")),
    S(query="cabbage napa raw", id="usda_napa_cabbage", zh="大白菜", en="Napa Cabbage", category="vegetable",
      aliases=("白菜", "娃娃菜")),
    S(query="cabbage bok choy raw", id="usda_bok_choy", zh="小白菜", en="Bok Choy", category="vegetable",
      aliases=("青菜", "上海青", "油菜")),
    S(query="lettuce cos or romaine raw", id="usda_lettuce", zh="生菜", en="Lettuce", category="vegetable",
      aliases=("莴苣", "罗马生菜")),
    S(query="tomato red ripe raw", id="usda_tomato", zh="番茄", en="Tomato", category="vegetable",
      aliases=("西红柿", "Tomato, raw"), serving_grams=150, serving_zh="一个(约150g)", serving_en="1 tomato (~150g)"),
    S(query="cucumber with peel raw", id="usda_cucumber", zh="黄瓜", en="Cucumber", category="vegetable",
      aliases=("青瓜",)),
    S(query="carrots raw", id="usda_carrot", zh="胡萝卜", en="Carrot", category="vegetable", aliases=("红萝卜",)),
    S(query="eggplant raw", id="usda_eggplant", zh="茄子", en="Eggplant", category="vegetable", aliases=("矮瓜",)),
    S(query="peppers sweet green raw", id="usda_green_pepper", zh="青椒", en="Green Pepper", category="vegetable",
      aliases=("甜椒", "柿子椒", "灯笼椒")),
    S(query="peppers sweet red raw", id="usda_red_pepper", zh="红椒", en="Red Pepper", category="vegetable",
      aliases=("红甜椒", "彩椒")),
    S(query="onions raw", id="usda_onion", zh="洋葱", en="Onion", category="vegetable", aliases=("圆葱",)),
    S(query="garlic raw", id="usda_garlic", zh="大蒜", en="Garlic", category="vegetable", aliases=("蒜", "蒜瓣")),
    S(query="ginger root raw", id="usda_ginger", zh="姜", en="Ginger", category="vegetable", aliases=("生姜",)),
    S(query="mushrooms white raw", id="usda_mushroom", zh="蘑菇", en="Mushroom", category="vegetable",
      aliases=("白蘑菇", "口蘑")),
    S(query="mushrooms shiitake cooked", id="usda_shiitake", zh="香菇", en="Shiitake", category="vegetable"),
    S(query="mushrooms oyster raw", id="usda_oyster_mushroom", zh="平菇", en="Oyster Mushroom", category="vegetable"),
    S(query="mushrooms enoki raw", id="usda_enoki", zh="金针菇", en="Enoki Mushroom", category="vegetable"),
    S(query="celery raw", id="usda_celery", zh="芹菜", en="Celery", category="vegetable", aliases=("西芹",)),
    S(query="waxgourd (winter melon) raw", id="usda_winter_melon", zh="冬瓜", en="Winter Melon", category="vegetable"),
    S(query="pumpkin raw", id="usda_pumpkin", zh="南瓜", en="Pumpkin", category="vegetable"),
    S(query="balsam-pear pods raw", id="usda_bitter_gourd", zh="苦瓜", en="Bitter Gourd", category="vegetable",
      aliases=("凉瓜",)),
    S(query="asparagus raw", id="usda_asparagus", zh="芦笋", en="Asparagus", category="vegetable", aliases=("石刁柏",)),
    S(query="mung beans sprouts raw", id="usda_bean_sprouts", zh="豆芽", en="Bean Sprouts", category="vegetable",
      aliases=("绿豆芽", "黄豆芽")),
    S(query="radishes raw", id="usda_radish", zh="白萝卜", en="Radish", category="vegetable", aliases=("萝卜",)),
    S(query="lotus root cooked", id="usda_lotus_root", zh="莲藕", en="Lotus Root", category="vegetable", aliases=("藕",)),
    S(query="seaweed kelp raw", id="usda_kelp", zh="海带", en="Kelp", category="vegetable", aliases=("昆布", "海带丝")),
    S(query="okra raw", id="usda_okra", zh="秋葵", en="Okra", category="vegetable"),
    S(query="seaweed laver raw", id="usda_nori", zh="紫菜", en="Nori", category="vegetable",
      aliases=("海苔", "紫菜(干)")),

    # ---------- 水果 ----------
    S(query="oranges raw", id="usda_orange", zh="橙子", en="Orange", category="fruit",
      serving_grams=150, serving_zh="一个(约150g)", serving_en="1 orange (~150g)"),
    S(query="tangerines raw", id="usda_tangerine", zh="橘子", en="Tangerine", category="fruit", aliases=("蜜桔", "柑橘")),
    S(query="grapes raw", id="usda_grape", zh="葡萄", en="Grape", category="fruit", aliases=("提子",)),
    S(query="watermelon raw", id="usda_watermelon", zh="西瓜", en="Watermelon", category="fruit"),
    S(query="strawberries raw", id="usda_strawberry", zh="草莓", en="Strawberry", category="fruit"),
    S(query="pears raw", id="usda_pear", zh="梨", en="Pear", category="fruit", aliases=("雪梨", "鸭梨")),
    S(query="peaches raw", id="usda_peach", zh="桃子", en="Peach", category="fruit", aliases=("水蜜桃",)),
    S(query="mangos raw", id="usda_mango", zh="芒果", en="Mango", category="fruit"),
    S(query="pineapple raw", id="usda_pineapple", zh="菠萝", en="Pineapple", category="fruit", aliases=("凤梨",)),
    S(query="kiwifruit green raw", id="usda_kiwi", zh="奇异果", en="Kiwi", category="fruit", aliases=("猕猴桃",)),
    S(query="blueberries raw", id="usda_blueberry", zh="蓝莓", en="Blueberry", category="fruit"),
    S(query="cherries sweet raw", id="usda_cherry", zh="樱桃", en="Cherry", category="fruit", aliases=("车厘子",)),
    S(query="lemon raw", id="usda_lemon", zh="柠檬", en="Lemon", category="fruit"),
    S(query="avocados raw", id="usda_avocado", zh="牛油果", en="Avocado", category="fruit", aliases=("鳄梨",)),
    S(query="lychees raw", id="usda_lychee", zh="荔枝", en="Lychee", category="fruit"),
    S(query="persimmons native raw", id="usda_persimmon", zh="柿子", en="Persimmon", category="fruit"),
    S(query="pomegranates raw", id="usda_pomegranate", zh="石榴", en="Pomegranate", category="fruit"),
    S(query="papayas raw", id="usda_papaya", zh="木瓜", en="Papaya", category="fruit"),
    S(query="cantaloupe raw", id="usda_cantaloupe", zh="哈密瓜", en="Cantaloupe", category="fruit", aliases=("甜瓜",)),
    S(query="grapefruit raw", id="usda_grapefruit", zh="葡萄柚", en="Grapefruit", category="fruit", aliases=("西柚",)),
    S(query="plums raw", id="usda_plum", zh="李子", en="Plum", category="fruit", aliases=("梅子",)),
    S(query="raspberries raw", id="usda_raspberry", zh="树莓", en="Raspberry", category="fruit", aliases=("覆盆子",)),
    S(query="dragon fruit (pitaya) raw", id="usda_dragon_fruit", zh="火龙果", en="Dragon Fruit", category="fruit"),

    # ---------- 坚果 / 种子 ----------
    S(query="almonds raw", id="usda_almond", zh="杏仁", en="Almond", category="nut", aliases=("巴旦木",),
      serving_grams=30, serving_zh="一小把(约30g)", serving_en="1 handful (~30g)"),
    S(query="walnuts english", id="usda_walnut", zh="核桃", en="Walnut", category="nut"),
    S(query="peanuts raw", id="usda_peanut", zh="花生", en="Peanut", category="nut"),
    S(query="cashew nuts raw", id="usda_cashew", zh="腰果", en="Cashew", category="nut"),
    S(query="pistachio nuts raw", id="usda_pistachio", zh="开心果", en="Pistachio", category="nut"),
    S(query="sunflower seed kernels", id="usda_sunflower_seed", zh="葵花籽", en="Sunflower Seed", category="nut",
      aliases=("瓜子",)),
    S(query="pumpkin and squash seed kernels", id="usda_pumpkin_seed", zh="南瓜子", en="Pumpkin Seed", category="nut"),
    S(query="sesame seeds whole dried", id="usda_sesame", zh="芝麻", en="Sesame", category="nut", aliases=("芝麻籽",)),
    S(query="chestnuts chinese raw", id="usda_chestnut", zh="板栗", en="Chestnut", category="nut", aliases=("栗子",)),
    S(query="hazelnuts raw", id="usda_hazelnut", zh="榛子", en="Hazelnut", category="nut"),

    # ---------- 油脂 / 调味 ----------
    S(query="oil olive", id="usda_olive_oil", zh="橄榄油", en="Olive Oil", category="fat",
      serving_grams=10, serving_zh="一勺(约10g)", serving_en="1 tbsp (~10g)"),
    S(query="oil vegetable", id="usda_vegetable_oil", zh="植物油", en="Vegetable Oil", category="fat",
      aliases=("食用油", "大豆油", "色拉油"), serving_grams=10, serving_zh="一勺(约10g)", serving_en="1 tbsp (~10g)"),
    S(query="oil peanut", id="usda_peanut_oil", zh="花生油", en="Peanut Oil", category="fat",
      serving_grams=10, serving_zh="一勺(约10g)", serving_en="1 tbsp (~10g)"),
    S(query="sugars granulated", id="usda_sugar", zh="白砂糖", en="Sugar", category="seasoning",
      aliases=("白糖", "砂糖", "糖"), serving_grams=5, serving_zh="一勺(约5g)", serving_en="1 tsp (~5g)"),
    S(query="honey", id="usda_honey", zh="蜂蜜", en="Honey", category="seasoning",
      serving_grams=20, serving_zh="一勺(约20g)", serving_en="1 tbsp (~20g)"),
    S(query="soy sauce made from soy and wheat", id="usda_soy_sauce", zh="酱油", en="Soy Sauce", category="seasoning",
      serving_grams=15, serving_zh="一勺(约15g)", serving_en="1 tbsp (~15g)"),
    S(query="vinegar distilled", id="usda_vinegar", zh="醋", en="Vinegar", category="seasoning", aliases=("食醋",)),
    S(query="catsup", id="usda_ketchup", zh="番茄酱", en="Ketchup", category="seasoning", aliases=("番茄沙司",)),
    S(query="salad dressing mayonnaise regular", id="usda_mayonnaise", zh="蛋黄酱", en="Mayonnaise", category="seasoning",
      aliases=("沙拉酱",)),

    # ---------- 饮料 ----------
    S(query="orange juice raw", id="usda_orange_juice", zh="橙汁", en="Orange Juice", category="beverage",
      serving_grams=250, serving_zh="一杯(约250g)", serving_en="1 cup (~250g)"),
    S(query="beverages coffee brewed prepared", id="usda_coffee", zh="黑咖啡", en="Coffee", category="beverage",
      aliases=("美式咖啡", "Coffee"), serving_grams=240, serving_zh="一杯(约240g)", serving_en="1 cup (~240g)"),
    S(query="alcoholic beverage beer regular", id="usda_beer", zh="啤酒", en="Beer", category="beverage",
      serving_grams=330, serving_zh="一罐(约330g)", serving_en="1 can (~330g)"),
    S(query="alcoholic beverage wine table red", id="usda_red_wine", zh="红酒", en="Red Wine", category="beverage",
      aliases=("葡萄酒",), serving_grams=150, serving_zh="一杯(约150g)", serving_en="1 glass (~150g)"),
]
# fmt: on


def _load_dotenv() -> None:
    """极简 .env 读取（避免为脚本引入额外依赖）。"""
    env_path = BACKEND_DIR / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def resolve_api_key(cli_key: str | None) -> str:
    _load_dotenv()
    return cli_key or os.environ.get("USDA_API_KEY") or os.environ.get("FDC_API_KEY") or "DEMO_KEY"


def extract_nutrients(nutrients: list[dict[str, Any]]) -> dict[str, float] | None:
    """把 foodNutrients 归一为 {kcal, protein, fat, carb}；无能量则返回 None。"""
    by_id: dict[int, dict[str, Any]] = {}
    for n in nutrients:
        nid = n.get("nutrientId")
        if isinstance(nid, int):
            by_id.setdefault(nid, n)

    def value(nid: int) -> float | None:
        n = by_id.get(nid)
        if n is None:
            return None
        v = n.get("value")
        return float(v) if isinstance(v, (int, float)) else None

    def to_kcal(nid: int) -> float | None:
        n = by_id.get(nid)
        v = value(nid)
        if v is None or n is None:
            return None
        unit = str(n.get("unitName", "")).upper()
        return v / KJ_PER_KCAL if unit == "KJ" else v

    energy = (
        to_kcal(NID_ENERGY_KCAL)
        or to_kcal(NID_ENERGY_KCAL_2)
        or to_kcal(NID_ENERGY)
        or to_kcal(NID_ENERGY_KJ)
    )
    if energy is None:
        return None
    return {
        "kcal": round(energy, 1),
        "protein": round(value(NID_PROTEIN) or 0.0, 1),
        "fat": round(value(NID_FAT) or 0.0, 1),
        "carb": round(value(NID_CARB) or 0.0, 1),
    }


def pick_food(foods: list[dict[str, Any]], query: str) -> dict[str, Any] | None:
    """从候选里挑一条：优先「查询词全覆盖 + 仅剩常规修饰词」的描述，其次按 USDA 相关度。

    单纯取最短描述会误选同科异种（如把 Broccoli 选成 Broccoli raab），
    因此先做一次严格过滤：描述必须包含查询的全部词，且多出来的词只能是
    USDA 惯用的加工 / 部位 / 状态修饰词。
    """
    candidates = [f for f in foods if extract_nutrients(f.get("foodNutrients", [])) is not None]
    if not candidates:
        return None

    q_tokens = set(_tokenize(query))

    def strict_ok(food: dict[str, Any]) -> bool:
        desc_tokens = set(_tokenize(str(food.get("description", ""))))
        if not q_tokens <= desc_tokens:
            return False
        return all(t in QUALIFIERS for t in desc_tokens - q_tokens)

    pool = [f for f in candidates if strict_ok(f)] or candidates

    def rank(food: dict[str, Any]) -> tuple[int, int]:
        foundation_first = 0 if food.get("dataType") == "Foundation" else 1
        return (foundation_first, len(str(food.get("description", ""))))

    return min(pool, key=rank)


def fetch(query: str, api_key: str, *, refresh: bool) -> dict[str, Any]:
    """拉取单条检索结果，带磁盘缓存。"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"{_normalize(query).replace('/', '_')}.json"
    if cache_file.exists() and not refresh:
        return json.loads(cache_file.read_text(encoding="utf-8"))

    import httpx

    params = {
        "query": query,
        "dataType": DATA_TYPES,
        "pageSize": PAGE_SIZE,
        "api_key": api_key,
    }
    resp = httpx.get(SEARCH_URL, params=params, timeout=30.0)
    resp.raise_for_status()
    payload = resp.json()
    cache_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    time.sleep(REQUEST_INTERVAL_SECONDS)
    return payload


def build_record(spec: FoodSpec, food: dict[str, Any]) -> FoodRecord:
    nutrients = extract_nutrients(food.get("foodNutrients", []))
    assert nutrients is not None
    return FoodRecord.model_validate(
        {
            "id": spec.id,
            "name": {"zh": spec.zh, "en": spec.en},
            "aliases": list(spec.aliases),
            "category": spec.category,
            "per_100g": nutrients,
            "standard_servings": [
                {
                    "label": {"zh": spec.serving_zh, "en": spec.serving_en},
                    "grams": spec.serving_grams,
                }
            ],
            "source": "usda",
            "fdc_id": str(food.get("fdcId", "")),
        }
    )


def load_existing() -> dict[str, dict[str, Any]]:
    if not OUT_PATH.exists():
        return {}
    return {item["id"]: item for item in json.loads(OUT_PATH.read_text(encoding="utf-8"))}


def warn_seed_collisions(records: list[FoodRecord]) -> None:
    """提示与手工 seed 条目重名的记录（不阻断——seed 加载在后，天然优先）。"""
    if not SEED_PATH.exists():
        return
    seed_keys: set[str] = set()
    for item in json.loads(SEED_PATH.read_text(encoding="utf-8")):
        seed_keys.add(_normalize(item["name"]["zh"]))
        seed_keys.add(_normalize(item["name"]["en"]))
        seed_keys.update(_normalize(a) for a in item.get("aliases", []))
    for record in records:
        keys = {_normalize(record.name.zh), _normalize(record.name.en)}
        keys.update(_normalize(a) for a in record.aliases)
        clash = keys & seed_keys
        if clash:
            print(f"  ! 与 seed 重名（手工优先）: {record.id} -> {sorted(clash)}")


def write_outputs(records: list[FoodRecord], stats: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = [r.model_dump(exclude_none=False) for r in sorted(records, key=lambda r: r.id)]
    OUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    meta = {
        "generated_by": "scripts/ingest_usda.py",
        "source": "USDA FoodData Central",
        "data_types": "Foundation, SR Legacy",
        "api": SEARCH_URL,
        "license": "Public domain (USDA FoodData Central)",
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "count": len(records),
        "data_type_breakdown": stats.get("breakdown", {}),
    }
    META_PATH.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def recompute_meta() -> int:
    if not OUT_PATH.exists():
        print(f"缺少 {OUT_PATH}，无法重建 meta。")
        return 1
    records = json.loads(OUT_PATH.read_text(encoding="utf-8"))
    breakdown: dict[str, int] = {"usda": len(records)}
    write_outputs(
        [FoodRecord.model_validate(r) for r in records],
        {"breakdown": breakdown},
    )
    print(f"已重建 {META_PATH.name}（{len(records)} 条）。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="摄入 USDA 天然食物营养数据")
    parser.add_argument("--check", action="store_true", help="只比对差异，不写盘")
    parser.add_argument("--recompute-meta", action="store_true", help="仅重建 _meta.json")
    parser.add_argument("--refresh", action="store_true", help="忽略本地缓存重新拉取")
    parser.add_argument("--api-key", default=None, help="USDA API Key（默认读 env / .env）")
    args = parser.parse_args(argv)

    if args.recompute_meta:
        return recompute_meta()

    api_key = resolve_api_key(args.api_key)
    if api_key == "DEMO_KEY":
        print("提示：未提供 API Key，使用 DEMO_KEY（限流 30 次/小时）。")

    records: list[FoodRecord] = []
    failed: list[tuple[str, str]] = []
    breakdown: dict[str, int] = {}

    for i, spec in enumerate(USDA_FOODS, 1):
        prefix = f"[{i:>3}/{len(USDA_FOODS)}] {spec.query}"
        try:
            payload = fetch(spec.query, api_key, refresh=args.refresh)
            food = pick_food(payload.get("foods", []), spec.query)
            if food is None:
                raise LookupError("无含能量的结果")
            records.append(build_record(spec, food))
            dt = str(food.get("dataType", "?"))
            breakdown[dt] = breakdown.get(dt, 0) + 1
            print(f"{prefix} -> {spec.id} ({dt}, fdcId={food.get('fdcId')})")
        except Exception as exc:  # noqa: BLE001 - 单条失败不中断整批
            failed.append((spec.id, f"{type(exc).__name__}: {exc}"))
            print(f"{prefix} !! 失败：{exc}")

    print(f"\n成功 {len(records)} 条，失败 {len(failed)} 条。")
    for fid, reason in failed:
        print(f"  - {fid}: {reason}")
    warn_seed_collisions(records)

    if args.check:
        existing = load_existing()
        new = {r.id: r.model_dump() for r in records}
        added = sorted(set(new) - set(existing))
        removed = sorted(set(existing) - set(new))
        changed = sorted(
            i for i in set(new) & set(existing) if new[i] != existing[i]
        )
        print(f"\n[check] 新增 {len(added)}，删除 {len(removed)}，变更 {len(changed)}")
        for i in added + removed + changed:
            print(f"  ~ {i}")
        return 0

    if not records:
        print("没有任何成功记录，未写盘。")
        return 1
    write_outputs(records, {"breakdown": breakdown})
    print(f"\n已写入 {OUT_PATH.relative_to(BACKEND_DIR)} 与 {META_PATH.name}。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
