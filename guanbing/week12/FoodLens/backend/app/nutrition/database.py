"""营养库：加载、索引与匹配。"""

from __future__ import annotations

import json
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

from ..schemas import FoodRecord

DATA_DIR = Path(__file__).parent / "data"


def _normalize(text: str) -> str:
    return text.strip().lower().replace(" ", "").replace("　", "")


class NutritionDatabase:
    """加载营养库并建立「名称 -> id」索引，支持精确 / 别名 / 模糊匹配。"""

    def __init__(self, records: list[FoodRecord]) -> None:
        self._records = list(records)
        self._by_id: dict[str, FoodRecord] = {}
        self._index: dict[str, str] = {}
        for record in self._records:
            self._by_id[record.id] = record
            for key in self._keys_for(record):
                self._index.setdefault(key, record.id)

    @staticmethod
    def _keys_for(record: FoodRecord) -> list[str]:
        keys = [_normalize(record.name.zh), _normalize(record.name.en)]
        keys.extend(_normalize(alias) for alias in record.aliases)
        return [key for key in keys if key]

    @property
    def records(self) -> list[FoodRecord]:
        return self._records

    def __len__(self) -> int:
        return len(self._records)

    def get(self, food_id: str) -> FoodRecord | None:
        return self._by_id.get(food_id)

    def match(self, name: str | None, threshold: float = 0.72) -> FoodRecord | None:
        """按 精确 -> 别名 -> 模糊 的顺序匹配。未命中返回 None。"""
        if not name:
            return None
        key = _normalize(name)
        if not key:
            return None
        if key in self._index:
            return self._by_id[self._index[key]]

        best_score = 0.0
        best_id: str | None = None
        for cand_key, record_id in self._index.items():
            score = SequenceMatcher(None, key, cand_key).ratio()
            shorter, longer = (
                (cand_key, key) if len(cand_key) <= len(key) else (key, cand_key)
            )
            # 子串加成：仅当被包含的名称覆盖输入的一半以上时生效，
            # 避免「量子纠缠炒饭」被别名「炒饭」误命中。
            if shorter and shorter in longer and len(shorter) / len(longer) >= 0.5:
                score = max(score, 0.9)
            if score > best_score:
                best_score, best_id = score, record_id
        if best_id is not None and best_score >= threshold:
            return self._by_id[best_id]
        return None


def _load_records() -> list[FoodRecord]:
    records: list[FoodRecord] = []
    for path in sorted(DATA_DIR.glob("*.json")):
        if path.name.startswith("_"):
            continue  # 跳过 _meta.json 等元数据文件
        raw = json.loads(path.read_text(encoding="utf-8"))
        records.extend(FoodRecord.model_validate(item) for item in raw)
    return records


@lru_cache
def get_database() -> NutritionDatabase:
    return NutritionDatabase(_load_records())
