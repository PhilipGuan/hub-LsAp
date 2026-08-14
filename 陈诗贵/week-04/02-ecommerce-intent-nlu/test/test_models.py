"""模型层测试：验证 TF-IDF / BERT 推理接口的输入输出契约。"""
from config import CATEGORY_NAME
from model.bert import predict_intent as bert_predict
from model.tfidf_ml import predict_intent as tfidf_predict


def test_tfidf_single():
    result = tfidf_predict("这件外套多少钱")
    assert isinstance(result, list) and len(result) == 1
    assert result[0] in CATEGORY_NAME


def test_tfidf_batch():
    result = tfidf_predict(["多少钱", "快递到哪了"])
    assert isinstance(result, list) and len(result) == 2
    assert all(r in CATEGORY_NAME for r in result)


def test_tfidf_empty():
    assert tfidf_predict([]) == []


def test_bert_single():
    result = bert_predict("我的订单发货了吗")
    assert isinstance(result, list) and len(result) == 1
    assert result[0] in CATEGORY_NAME


def test_bert_batch():
    result = bert_predict(["帮我转人工", "能退货吗"])
    assert isinstance(result, list) and len(result) == 2
    assert all(r in CATEGORY_NAME for r in result)


def test_bert_empty():
    assert bert_predict([]) == []
