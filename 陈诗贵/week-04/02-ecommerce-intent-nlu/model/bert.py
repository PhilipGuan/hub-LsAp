"""BERT 微调模型推理。

懒加载：首次调用 predict_intent 时才加载 tokenizer 与模型权重。
"""
from typing import List, Union

import numpy as np
import torch
from transformers import AutoTokenizer, BertForSequenceClassification

from config import (
    BERT_MODEL_PERTRAINED_PATH,
    BERT_MODEL_PKL_PATH,
    CATEGORY_NAME,
    BERT_MAX_LENGTH,
)

_device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
_tokenizer = None
_model = None


def _get_model():
    global _tokenizer, _model
    if _model is None:
        _tokenizer = AutoTokenizer.from_pretrained(BERT_MODEL_PERTRAINED_PATH)
        _model = BertForSequenceClassification.from_pretrained(
            BERT_MODEL_PERTRAINED_PATH,
            num_labels=len(CATEGORY_NAME),
        )
        _model.load_state_dict(torch.load(BERT_MODEL_PKL_PATH, map_location=_device))
        _model.to(_device)
        _model.eval()
    return _tokenizer, _model


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

    tokenizer, model = _get_model()
    encodings = tokenizer(
        texts,
        truncation=True,
        padding=True,
        max_length=BERT_MAX_LENGTH,
        return_tensors="pt",
    )
    input_ids = encodings["input_ids"].to(_device)
    attention_mask = encodings["attention_mask"].to(_device)

    with torch.no_grad():
        logits = model(input_ids=input_ids, attention_mask=attention_mask).logits
        preds = logits.detach().cpu().numpy().argmax(axis=1).tolist()

    return [CATEGORY_NAME[p] for p in preds]
