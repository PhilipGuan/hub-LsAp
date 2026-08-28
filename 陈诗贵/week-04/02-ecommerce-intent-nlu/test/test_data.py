"""数据集质量测试：对应测试用例 DATA-01 ~ DATA-05。"""
import os

import pandas as pd

from config import CATEGORY_NAME, DATASET_PATH


def _load():
    assert os.path.exists(DATASET_PATH), f"数据集不存在：{DATASET_PATH}"
    return pd.read_csv(DATASET_PATH, sep="\t", header=None)


def test_dataset_nonempty():
    df = _load()
    assert len(df) >= 400, "DATA-01 失败：样本数不足 400"


def test_labels_valid():
    df = _load()
    invalid = set(df[1].unique()) - set(CATEGORY_NAME)
    assert not invalid, f"DATA-02 失败：存在非法标签 {invalid}"


def test_each_category_min_samples():
    df = _load()
    counts = df[1].value_counts().to_dict()
    for c in CATEGORY_NAME:
        assert counts.get(c, 0) >= 30, f"DATA-03 失败：{c} 样本数不足 30"


def test_no_empty_or_duplicate():
    df = _load()
    assert df[0].isna().sum() == 0, "DATA-04 失败：存在空值"
    assert (df[0].str.strip() == "").sum() == 0, "DATA-04 失败：存在空白文本"
    assert df.duplicated().sum() == 0, "DATA-04 失败：存在重复行"
