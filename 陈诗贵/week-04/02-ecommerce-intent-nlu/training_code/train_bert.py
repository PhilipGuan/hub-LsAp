"""训练并微调 BERT 意图分类模型，保存最优权重。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import torch
from datasets import Dataset
from sklearn.metrics import accuracy_score, f1_score
from sklearn.model_selection import train_test_split
from transformers import (
    BertForSequenceClassification,
    BertTokenizer,
    Trainer,
    TrainingArguments,
)

from config import (
    BERT_BATCH_SIZE,
    BERT_MAX_LENGTH,
    BERT_MODEL_PERTRAINED_PATH,
    BERT_MODEL_PKL_PATH,
    BERT_NUM_EPOCHS,
    BERT_OUTPUT_DIR,
    CATEGORY_ID,
    CATEGORY_NAME,
    DATASET_PATH,
    RANDOM_SEED,
    TEST_SIZE,
)


def main():
    data = pd.read_csv(DATASET_PATH, sep="\t", header=None)
    texts = list(data[0].values)

    # 直接用 CATEGORY_ID 映射，保证标签索引与 CATEGORY_NAME 顺序一致
    labels = [CATEGORY_ID[name] for name in data[1].values]

    x_train, x_test, y_train, y_test = train_test_split(
        texts, labels,
        test_size=TEST_SIZE,
        stratify=labels,
        random_state=RANDOM_SEED,
    )

    tokenizer = BertTokenizer.from_pretrained(BERT_MODEL_PERTRAINED_PATH)
    model = BertForSequenceClassification.from_pretrained(
        BERT_MODEL_PERTRAINED_PATH,
        num_labels=len(CATEGORY_NAME),
    )

    train_enc = tokenizer(x_train, truncation=True, padding=True, max_length=BERT_MAX_LENGTH)
    test_enc = tokenizer(x_test, truncation=True, padding=True, max_length=BERT_MAX_LENGTH)

    train_dataset = Dataset.from_dict({
        "input_ids": train_enc["input_ids"],
        "attention_mask": train_enc["attention_mask"],
        "labels": y_train,
    })
    test_dataset = Dataset.from_dict({
        "input_ids": test_enc["input_ids"],
        "attention_mask": test_enc["attention_mask"],
        "labels": y_test,
    })

    def compute_metrics(eval_pred):
        logits, labels = eval_pred
        preds = np.argmax(logits, axis=-1)
        return {
            "accuracy": accuracy_score(labels, preds),
            "macro_f1": f1_score(labels, preds, average="macro"),
        }

    training_args = TrainingArguments(
        output_dir=BERT_OUTPUT_DIR,
        num_train_epochs=BERT_NUM_EPOCHS,
        per_device_train_batch_size=BERT_BATCH_SIZE,
        per_device_eval_batch_size=BERT_BATCH_SIZE,
        warmup_steps=100,
        weight_decay=0.01,
        logging_dir="./logs",
        logging_steps=10,
        eval_strategy="epoch",
        save_strategy="epoch",
        load_best_model_at_end=True,
        metric_for_best_model="macro_f1",
        report_to="none",
    )

    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=test_dataset,
        compute_metrics=compute_metrics,
    )

    trainer.train()
    print(trainer.evaluate())

    best_path = trainer.state.best_model_checkpoint
    if best_path:
        best_model = BertForSequenceClassification.from_pretrained(best_path)
        os.makedirs(os.path.dirname(BERT_MODEL_PKL_PATH), exist_ok=True)
        torch.save(best_model.state_dict(), BERT_MODEL_PKL_PATH)
        print(f"最优模型已保存至 {BERT_MODEL_PKL_PATH}")
    else:
        print("未找到最优 checkpoint，请检查训练配置")


if __name__ == "__main__":
    main()
