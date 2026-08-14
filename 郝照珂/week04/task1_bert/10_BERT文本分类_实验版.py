"""第四周作业1：BERT 文本分类微调与超参数对比。"""
import argparse
import csv
import json
import random
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder
from torch.utils.data import DataLoader, Dataset
from transformers import BertForSequenceClassification, BertTokenizer

ROOT = Path(__file__).resolve().parents[3]
DATA_PATH = ROOT / "Week1-课程介绍与大模型基础" / "02课程介绍与大模型基础" / "03-代码" / "dataset.csv"
MODEL_PATH = ROOT / "week04" / "models" / "bert-base-chinese-ms"
OUTPUT_DIR = Path(__file__).resolve().parent / "outputs"


class TextDataset(Dataset):
    def __init__(self, encodings, labels):
        self.encodings, self.labels = encodings, labels

    def __len__(self):
        return len(self.labels)

    def __getitem__(self, index):
        item = {key: torch.tensor(value[index]) for key, value in self.encodings.items()}
        item["labels"] = torch.tensor(int(self.labels[index]))
        return item


def seed_everything(seed=42):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


@torch.no_grad()
def evaluate(model, loader, device):
    model.eval()
    correct = total = 0
    losses = []
    for batch in loader:
        batch = {k: v.to(device) for k, v in batch.items()}
        output = model(**batch)
        losses.append(output.loss.item())
        correct += (output.logits.argmax(-1) == batch["labels"]).sum().item()
        total += batch["labels"].numel()
    return float(np.mean(losses)), correct / total


def run_experiment(name, learning_rate, batch_size, epochs, save_model=False):
    seed_everything()
    frame = pd.read_csv(DATA_PATH, sep="\t", header=None).iloc[:500]
    encoder = LabelEncoder()
    labels = encoder.fit_transform(frame[1].values)
    texts = frame[0].astype(str).tolist()
    x_train, x_test, y_train, y_test = train_test_split(
        texts, labels, test_size=0.2, stratify=labels, random_state=42
    )
    tokenizer = BertTokenizer.from_pretrained(MODEL_PATH, local_files_only=True)
    train_data = TextDataset(tokenizer(x_train, truncation=True, padding="max_length", max_length=64), y_train)
    test_data = TextDataset(tokenizer(x_test, truncation=True, padding="max_length", max_length=64), y_test)
    generator = torch.Generator().manual_seed(42)
    train_loader = DataLoader(train_data, batch_size=batch_size, shuffle=True, generator=generator)
    test_loader = DataLoader(test_data, batch_size=batch_size)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = BertForSequenceClassification.from_pretrained(
        MODEL_PATH, num_labels=len(encoder.classes_), local_files_only=True
    ).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate, weight_decay=0.01)
    history = []
    started = time.time()
    print(f"实验={name} | device={device} | lr={learning_rate} | batch={batch_size} | 类别数={len(encoder.classes_)}")
    for epoch in range(1, epochs + 1):
        model.train()
        train_losses = []
        for batch in train_loader:
            batch = {k: v.to(device) for k, v in batch.items()}
            optimizer.zero_grad()
            output = model(**batch)
            output.loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            train_losses.append(output.loss.item())
        eval_loss, accuracy = evaluate(model, test_loader, device)
        row = {"experiment": name, "epoch": epoch, "learning_rate": learning_rate,
               "batch_size": batch_size, "train_loss": float(np.mean(train_losses)),
               "eval_loss": eval_loss, "accuracy": accuracy}
        history.append(row)
        print(f"Epoch {epoch}/{epochs} | train_loss={row['train_loss']:.4f} | eval_loss={eval_loss:.4f} | accuracy={accuracy:.4f}")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # 作业提交只需代码和实验指标，默认不保存约 390 MB 的完整模型权重。
    # 如需后续推理，可通过 --save-model 显式保存。
    if save_model:
        model.save_pretrained(OUTPUT_DIR / f"model_{name}")
        tokenizer.save_pretrained(OUTPUT_DIR / f"model_{name}")
    result = {"experiment": name, "device": str(device), "samples": 500,
              "train_samples": 400, "test_samples": 100, "num_labels": len(encoder.classes_),
              "labels": encoder.classes_.tolist(), "seconds": round(time.time() - started, 2),
              "best_accuracy": max(x["accuracy"] for x in history), "history": history}
    (OUTPUT_DIR / f"result_{name}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--learning-rate", type=float, required=True)
    parser.add_argument("--batch-size", type=int, required=True)
    parser.add_argument("--epochs", type=int, default=4)
    parser.add_argument("--save-model", action="store_true", help="保存约390 MB的微调模型权重")
    args = parser.parse_args()
    run_experiment(args.name, args.learning_rate, args.batch_size, args.epochs, args.save_model)
