"""FastAPI 推理服务入口：提供 TF-IDF 与 BERT 两条意图识别路由。"""
import time
import traceback

from fastapi import FastAPI

from data_schema import TextClassifyRequest, TextClassifyResponse
from logger import logger
from model.bert import predict_intent as bert_predict
from model.tfidf_ml import predict_intent as tfidf_predict

app = FastAPI(title="电商意图识别 NLU")


@app.get("/")
def read_root():
    return {"service": "ecommerce-intent-nlu", "status": "ok"}


def _classify(req: TextClassifyRequest, model_predict) -> TextClassifyResponse:
    start = time.time()
    response = TextClassifyResponse(
        request_id=req.request_id,
        request_text=req.request_text,
        classify_result=[],
        classify_time=0.0,
        error_msg="",
    )
    logger.info(f"{req.request_id} {req.request_text}")
    try:
        response.classify_result = model_predict(req.request_text)
        response.error_msg = "ok"
    except Exception:
        response.classify_result = []
        response.error_msg = traceback.format_exc()
    response.classify_time = round(time.time() - start, 3)
    return response


@app.post("/v1/intent/tfidf")
def tfidf_classify(req: TextClassifyRequest) -> TextClassifyResponse:
    return _classify(req, tfidf_predict)


@app.post("/v1/intent/bert")
def bert_classify(req: TextClassifyRequest) -> TextClassifyResponse:
    return _classify(req, bert_predict)
