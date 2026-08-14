"""训练 TF-IDF + LinearSVC baseline 模型，并输出验证集评估结果。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jieba
import pandas as pd
from joblib import dump
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import accuracy_score, classification_report
from sklearn.model_selection import train_test_split
from sklearn.svm import LinearSVC

from config import (
    DATASET_PATH,
    LOCAL_STOPWORDS_PATH,
    REMOTE_STOPWORDS_URL,
    RANDOM_SEED,
    TEST_SIZE,
    TFIDF_MODEL_PKL_PATH,
)


def _load_stopwords():
    try:
        return pd.read_csv(LOCAL_STOPWORDS_PATH, header=None)[0].values
    except Exception:
        return pd.read_csv(REMOTE_STOPWORDS_URL, header=None)[0].values


def main():
    data = pd.read_csv(DATASET_PATH, sep="\t", header=None)
    stopwords = _load_stopwords()

    # 分词 + 停用词过滤
    data[0] = data[0].apply(
        lambda x: " ".join([w for w in jieba.lcut(x) if w.strip() and w not in stopwords])
    )

    x_train, x_test, y_train, y_test = train_test_split(
        data[0], data[1],
        test_size=TEST_SIZE,
        stratify=data[1],
        random_state=RANDOM_SEED,
    )

    tfidf = TfidfVectorizer(ngram_range=(1, 2))
    x_train_tfidf = tfidf.fit_transform(x_train)
    x_test_tfidf = tfidf.transform(x_test)

    model = LinearSVC(random_state=RANDOM_SEED)
    model.fit(x_train_tfidf, y_train)

    preds = model.predict(x_test_tfidf)
    print("Accuracy:", accuracy_score(y_test, preds))
    print(classification_report(y_test, preds))

    os.makedirs(os.path.dirname(TFIDF_MODEL_PKL_PATH), exist_ok=True)
    dump((tfidf, model), TFIDF_MODEL_PKL_PATH)
    print(f"模型已保存至 {TFIDF_MODEL_PKL_PATH}")


if __name__ == "__main__":
    main()
