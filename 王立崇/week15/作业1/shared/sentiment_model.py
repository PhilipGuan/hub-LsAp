"""可插拔的本地微调情感分类模型（HuggingFace transformers）。

进程1 的协议不变，仍然只消费 SentimentRequest、产出 SentimentResult；
这里只是把"怎么判断"换成你自己的微调模型。

启用方式（环境变量）：

    SENTIMENT_MODEL_PATH=/models/my-sentiment      # 本地目录或 HF 模型名
    SENTIMENT_DEVICE=-1                            # -1=CPU，0=第一张 GPU
    SENTIMENT_LABEL_MAP={"LABEL_0": "negative", "LABEL_1": "positive"}

需要额外安装：pip install -r requirements.model.txt

模型输出标签的常见写法（positive / LABEL_0 / 1 star / 正向 …）会自动归一化；
无法判断时会明确提示用 SENTIMENT_LABEL_MAP 指定，随后由进程1 回退到 LLM 或词典。
"""
from __future__ import annotations

import json
import os
import re
import threading

from .runtime import log

_ALIASES: dict[str, tuple[str, ...]] = {
    "positive": ("positive", "pos", "正向", "积极", "好评", "4 star", "5 star"),
    "negative": ("negative", "neg", "负向", "消极", "差评", "1 star", "2 star"),
    "neutral": ("neutral", "neu", "中性", "3 star"),
}

_pipeline = None
_load_error: str | None = None
_lock = threading.Lock()


def enabled() -> bool:
    return bool(os.getenv("SENTIMENT_MODEL_PATH", "").strip())


def _device() -> int:
    try:
        return int(os.getenv("SENTIMENT_DEVICE", "-1"))
    except ValueError:
        return -1


def _label_map() -> dict[str, str]:
    raw = os.getenv("SENTIMENT_LABEL_MAP", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        log("SENTIMENT_LABEL_MAP 不是合法 JSON，已忽略")
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): str(value) for key, value in data.items()}


def _get_pipeline():
    """懒加载：第一次请求时加载模型，进程内复用。"""
    global _pipeline, _load_error

    if _pipeline is not None:
        return _pipeline
    if _load_error:
        raise RuntimeError(_load_error)

    with _lock:
        if _pipeline is not None:
            return _pipeline
        if _load_error:
            raise RuntimeError(_load_error)

        path = os.getenv("SENTIMENT_MODEL_PATH", "").strip()
        if not path:
            raise RuntimeError("未设置 SENTIMENT_MODEL_PATH")

        try:
            from transformers import pipeline  # 可选依赖

            _pipeline = pipeline(
                "text-classification",
                model=path,
                tokenizer=path,
                device=_device(),
                truncation=True,
                max_length=512,
                top_k=None,  # 返回所有标签，便于处理未知标签
            )
        except Exception as exc:
            _load_error = (
                f"加载本地情感模型失败（{path}）：{exc}。"
                "请确认已安装 requirements.model.txt 且模型路径正确。"
            )
            raise RuntimeError(_load_error) from exc

    return _pipeline


def _as_scores(raw) -> list[tuple[str, float]]:
    items = raw
    if isinstance(items, dict):
        items = [items]
    if isinstance(items, list) and items and isinstance(items[0], list):
        items = items[0]

    scores: list[tuple[str, float]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        label = item.get("label")
        try:
            score = float(item.get("score", 0.0))
        except (TypeError, ValueError):
            continue
        if label is not None:
            scores.append((str(label), score))
    return scores


def _numeric_convention(labels: list[str]) -> dict[str, str]:
    """LABEL_0/1/2 这类无语义标签的常见顺序约定。

    二分类：0=negative，1=positive；
    多分类：0=negative，中间=neutral，最大编号=positive。
    可用 SENTIMENT_LABEL_MAP 覆盖。
    """
    indexes: dict[str, int] = {}
    for label in labels:
        match = re.fullmatch(r"\s*label[_\- ]?([0-9]+)\s*", label, re.IGNORECASE)
        if match:
            indexes[label] = int(match.group(1))
    if len(indexes) < 2:
        return {}

    max_index = max(indexes.values())
    mapping: dict[str, str] = {}
    for label, index in indexes.items():
        if max_index == 1:
            mapping[label] = "negative" if index == 0 else "positive"
        elif index == 0:
            mapping[label] = "negative"
        elif index == max_index:
            mapping[label] = "positive"
        else:
            mapping[label] = "neutral"
    return mapping


def _normalize_label(label: str, mapping: dict[str, str]) -> str | None:
    raw = str(label).strip()
    if raw in mapping:
        return _normalize_label(mapping[raw], {})

    lowered_map = {key.lower(): value for key, value in mapping.items()}
    if raw.lower() in lowered_map:
        return _normalize_label(lowered_map[raw.lower()], {})

    lowered = raw.lower()

    # 星级的常见写法：1-2 负向，3 中性，4-5 正向
    star = re.fullmatch(r"([1-5])\s*stars?", lowered)
    if star:
        value = int(star.group(1))
        return {"1": "negative", "2": "negative", "3": "neutral", "4": "positive", "5": "positive"}[
            str(value)
        ]

    for sentiment, aliases in _ALIASES.items():
        for alias in aliases:
            if lowered == alias or alias in lowered:
                return sentiment
    return None


def classify(text: str) -> tuple[str, float, str]:
    """返回 (sentiment, confidence, reason)，与词典规则保持同一返回形状。"""
    if not text.strip():
        raise ValueError("空文本无法分类")

    raw = _get_pipeline()(text)
    scores = _as_scores(raw)
    if not scores:
        raise ValueError("模型没有返回可用的标签分数")

    # 先用 LABEL_0/1/2 的常见约定，再让用户显式配置覆盖
    mapping = {
        **_numeric_convention([label for label, _ in scores]),
        **_label_map(),
    }
    recognized: list[tuple[str, float, str]] = []
    for raw_label, score in scores:
        normalized = _normalize_label(raw_label, mapping)
        if normalized is None:
            log(
                f"本地模型标签 {raw_label!r} 无法映射到 positive/negative/neutral，"
                "可用 SENTIMENT_LABEL_MAP 指定"
            )
            continue
        recognized.append((normalized, score, raw_label))

    if not recognized:
        raise ValueError("模型输出标签无法映射到 positive/negative/neutral")

    sentiment, score, raw_label = max(recognized, key=lambda item: item[1])
    confidence = max(0.0, min(1.0, float(score)))
    return sentiment, confidence, f"本地模型标签 {raw_label}（score={confidence:.3f}）"
