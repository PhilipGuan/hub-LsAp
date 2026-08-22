"""TF-IDF + LinearSVC baseline 推理。

采用懒加载：首次调用 predict_intent 时才加载模型权重，
避免 import 阶段因权重缺失而报错，便于单测与解耦。
"""
from typing import List, Union

import jieba
import pandas as pd
from joblib import load

from config import (
    TFIDF_MODEL_PKL_PATH,
    LOCAL_STOPWORDS_PATH,
    REMOTE_STOPWORDS_URL,
)

_model = None          # (tfidf_vectorizer, classifier)
_stopwords = None      # 停用词数组


def _get_stopwords():
    global _stopwords
    if _stopwords is None:
        try:
            _stopwords = pd.read_csv(LOCAL_STOPWORDS_PATH, header=None)[0].values
        except Exception:
            _stopwords = pd.read_csv(REMOTE_STOPWORDS_URL, header=None)[0].values
    return _stopwords


def _get_model():
    global _model
    if _model is None:
        _model = load(TFIDF_MODEL_PKL_PATH)
    return _model


def _preprocess(texts: List[str]) -> List[str]:
    stopwords = _get_stopwords()
    cleaned = []
    for text in texts:
        words = [w for w in jieba.lcut(text) if w.strip() and w not in stopwords]
        cleaned.append(" ".join(words))
    return cleaned


def predict_intent(request_text: Union[str, List[str]]) -> List[str]:
    """对单条或批量文本返回意图标签列表。"""
    if isinstance(request_text, str):
        texts = [request_text]
    elif isinstance(request_text, list):
        texts = list(request_text)
    else:
        raise TypeError("request_text 仅支持 str 或 List[str]")

    if not texts:
        return []

    tfidf, classifier = _get_model()
    features = tfidf.transform(_preprocess(texts))
    preds = classifier.predict(features)
    return list(preds)
