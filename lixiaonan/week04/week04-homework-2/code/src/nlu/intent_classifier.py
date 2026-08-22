"""
模块二：基于 BERT 的意图识别模型（NLU）
支持电商场景下 6 类用户意图的分类
"""

import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader
from transformers import BertTokenizer, BertModel, get_linear_schedule_with_warmup
from sklearn.metrics import f1_score, classification_report
from typing import List, Dict, Tuple
import numpy as np

from config import BERT_MODEL_NAME, INTENT_LABELS, INTENT_MODEL_PATH


# ─────────────────────────────────────────────────────────
# 1. 数据集类
# ─────────────────────────────────────────────────────────
class IntentDataset(Dataset):
    """
    意图识别数据集
    输入格式：
        {"text": "我的包裹到哪了？", "label": 0}
    """

    def __init__(
        self,
        samples: List[Dict],
        tokenizer: BertTokenizer,
        max_len: int = 64,
    ):
        self.samples = samples
        self.tokenizer = tokenizer
        self.max_len = max_len

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        text = self.samples[idx]["text"]
        label = self.samples[idx]["label"]

        encoding = self.tokenizer(
            text,
            max_length=self.max_len,
            padding="max_length",
            truncation=True,
            return_tensors="pt",
        )

        return {
            "input_ids": encoding["input_ids"].squeeze(0),
            "attention_mask": encoding["attention_mask"].squeeze(0),
            "token_type_ids": encoding["token_type_ids"].squeeze(0),
            "label": torch.tensor(label, dtype=torch.long),
        }


# ─────────────────────────────────────────────────────────
# 2. BERT 意图分类模型
# ─────────────────────────────────────────────────────────
class BertIntentClassifier(nn.Module):
    """
    BERT + 分类头 意图识别模型

    架构：
        用户输入 → BERT 编码（[CLS] 向量）→ Dropout → 线性层 → Softmax → 意图类别
    """

    def __init__(
        self,
        model_name: str = BERT_MODEL_NAME,
        num_classes: int = len(INTENT_LABELS),
        dropout: float = 0.3,
    ):
        super().__init__()
        self.bert = BertModel.from_pretrained(model_name)
        hidden_size = self.bert.config.hidden_size  # 768

        self.dropout = nn.Dropout(dropout)
        self.classifier = nn.Linear(hidden_size, num_classes)

    def forward(
        self,
        input_ids: torch.Tensor,          # (B, L)
        attention_mask: torch.Tensor,     # (B, L)
        token_type_ids: torch.Tensor,     # (B, L)
        label: torch.Tensor = None,       # (B,)  训练时传入
    ) -> Dict:
        outputs = self.bert(
            input_ids=input_ids,
            attention_mask=attention_mask,
            token_type_ids=token_type_ids,
        )
        # 取 [CLS] 位置的向量作为句子表示
        cls_output = outputs.last_hidden_state[:, 0, :]   # (B, 768)
        cls_output = self.dropout(cls_output)
        logits = self.classifier(cls_output)              # (B, num_classes)

        result = {"logits": logits}
        if label is not None:
            loss_fn = nn.CrossEntropyLoss()
            result["loss"] = loss_fn(logits, label)

        return result


# ─────────────────────────────────────────────────────────
# 3. 训练函数
# ─────────────────────────────────────────────────────────
def train_intent_classifier(
    train_samples: List[Dict],
    val_samples: List[Dict],
    epochs: int = 5,
    batch_size: int = 32,
    lr: float = 2e-5,
    save_path: str = INTENT_MODEL_PATH,
) -> BertIntentClassifier:

    tokenizer = BertTokenizer.from_pretrained(BERT_MODEL_NAME)
    train_loader = DataLoader(
        IntentDataset(train_samples, tokenizer), batch_size=batch_size, shuffle=True
    )
    val_loader = DataLoader(
        IntentDataset(val_samples, tokenizer), batch_size=batch_size
    )

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = BertIntentClassifier().to(device)

    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=0.01)
    total_steps = len(train_loader) * epochs
    scheduler = get_linear_schedule_with_warmup(
        optimizer, num_warmup_steps=total_steps // 10, num_training_steps=total_steps
    )

    best_f1 = 0.0
    for epoch in range(epochs):
        # ── 训练 ──
        model.train()
        total_loss = 0.0
        for batch in train_loader:
            batch = {k: v.to(device) for k, v in batch.items()}
            out = model(**batch)
            loss = out["loss"]
            optimizer.zero_grad()
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            scheduler.step()
            total_loss += loss.item()

        # ── 验证 ──
        val_f1, report = evaluate_intent(model, val_loader, device)
        print(f"Epoch {epoch+1}/{epochs}  loss={total_loss/len(train_loader):.4f}  val_f1={val_f1:.4f}")

        if val_f1 > best_f1:
            best_f1 = val_f1
            torch.save(model.state_dict(), f"{save_path}/intent_model.pt")
            tokenizer.save_pretrained(save_path)
            print(f"  ✓ 保存最优模型（f1={val_f1:.4f}）")

    print(f"\n训练完成，最优 F1-score: {best_f1:.4f}")
    return model


# ─────────────────────────────────────────────────────────
# 4. 评估函数
# ─────────────────────────────────────────────────────────
def evaluate_intent(model, dataloader, device) -> Tuple[float, str]:
    model.eval()
    all_preds, all_labels = [], []
    with torch.no_grad():
        for batch in dataloader:
            labels = batch.pop("label").cpu().numpy()
            batch = {k: v.to(device) for k, v in batch.items()}
            out = model(**batch)
            preds = out["logits"].argmax(dim=-1).cpu().numpy()
            all_preds.extend(preds)
            all_labels.extend(labels)

    f1 = f1_score(all_labels, all_preds, average="macro")
    report = classification_report(
        all_labels, all_preds,
        target_names=list(INTENT_LABELS.values())
    )
    return f1, report


# ─────────────────────────────────────────────────────────
# 5. 推理接口
# ─────────────────────────────────────────────────────────
class IntentPredictor:
    """
    意图识别推理器
    输入用户文本，输出意图类别和置信度
    """

    def __init__(self, model_path: str = INTENT_MODEL_PATH):
        self.tokenizer = BertTokenizer.from_pretrained(model_path)
        self.model = BertIntentClassifier()
        self.model.load_state_dict(
            torch.load(f"{model_path}/intent_model.pt", map_location="cpu")
        )
        self.model.eval()

    def predict(self, text: str) -> Dict:
        """
        返回：
            {
                "intent_id": 0,
                "intent_name": "INT_001_物流查询",
                "confidence": 0.96,
                "all_scores": {意图名: 置信度, ...}
            }
        """
        encoding = self.tokenizer(
            text, return_tensors="pt", max_length=64, truncation=True, padding=True
        )
        with torch.no_grad():
            out = self.model(
                input_ids=encoding["input_ids"],
                attention_mask=encoding["attention_mask"],
                token_type_ids=encoding["token_type_ids"],
            )
        probs = torch.softmax(out["logits"], dim=-1).squeeze(0).numpy()
        intent_id = int(np.argmax(probs))

        return {
            "intent_id": intent_id,
            "intent_name": INTENT_LABELS[intent_id],
            "confidence": float(probs[intent_id]),
            "all_scores": {
                INTENT_LABELS[i]: round(float(p), 4)
                for i, p in enumerate(probs)
            },
        }


# ─────────────────────────────────────────────────────────
# 演示数据（少量示例）
# ─────────────────────────────────────────────────────────
DEMO_TRAIN_SAMPLES = [
    {"text": "我的包裹到哪了", "label": 0},
    {"text": "快递什么时候到", "label": 0},
    {"text": "物流查询", "label": 0},
    {"text": "想申请退款", "label": 1},
    {"text": "这个商品可以退吗", "label": 1},
    {"text": "七天无理由退货怎么办理", "label": 1},
    {"text": "这款手机支持5G吗", "label": 2},
    {"text": "内存是多大的", "label": 2},
    {"text": "这个商品有什么颜色", "label": 2},
    {"text": "双十一有什么折扣", "label": 3},
    {"text": "现在有优惠券吗", "label": 3},
    {"text": "什么时候打折", "label": 3},
    {"text": "客服态度太差了", "label": 4},
    {"text": "产品质量有问题", "label": 4},
    {"text": "投诉一下你们的服务", "label": 4},
    {"text": "你好", "label": 5},
    {"text": "谢谢", "label": 5},
]


if __name__ == "__main__":
    print("意图识别模型结构示例")
    model = BertIntentClassifier()
    total_params = sum(p.numel() for p in model.parameters())
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print(f"  总参数量：{total_params:,}")
    print(f"  可训练参数：{trainable:,}")
    print(f"\n意图类别：")
    for idx, name in INTENT_LABELS.items():
        print(f"  [{idx}] {name}")
