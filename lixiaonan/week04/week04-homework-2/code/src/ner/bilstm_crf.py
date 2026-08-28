"""
模块一：BiLSTM + CRF 命名实体识别模型
用于识别电商文本中的商品名、品牌名、属性词、价格、日期等实体
"""

import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader
from torchcrf import CRF
from transformers import BertTokenizerFast
from seqeval.metrics import f1_score, classification_report
from typing import List, Tuple, Dict
import numpy as np

from config import BERT_MODEL_NAME, NER_LABELS, NER_LABEL2ID, NER_ID2LABEL


# ─────────────────────────────────────────────────────────
# 1. 数据集类
# ─────────────────────────────────────────────────────────
class NERDataset(Dataset):
    """
    NER 数据集
    输入格式（BIO 标注）：
        tokens: ["耐", "克", "运", "动", "鞋"]
        labels: ["B-BRAND", "I-BRAND", "B-PRODUCT", "I-PRODUCT", "I-PRODUCT"]
    """

    def __init__(
        self,
        samples: List[Dict],
        tokenizer: BertTokenizerFast,
        max_len: int = 128,
    ):
        self.samples = samples
        self.tokenizer = tokenizer
        self.max_len = max_len

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        tokens = self.samples[idx]["tokens"]   # List[str]，逐字符
        labels = self.samples[idx]["labels"]   # List[str]，BIO 标签

        encoding = self.tokenizer(
            tokens,
            is_split_into_words=True,
            max_length=self.max_len,
            padding="max_length",
            truncation=True,
            return_tensors="pt",
        )

        # 对齐 subword 与原始 token 的标签（取每个 word 第一个 subword 的标签）
        word_ids = encoding.word_ids(batch_index=0)
        label_ids = []
        prev_word_id = None
        for word_id in word_ids:
            if word_id is None:
                label_ids.append(-100)          # [CLS] / [SEP] / [PAD] 忽略
            elif word_id != prev_word_id:
                label_ids.append(NER_LABEL2ID[labels[word_id]])
            else:
                label_ids.append(-100)          # 同一 word 后续 subword 忽略
            prev_word_id = word_id

        return {
            "input_ids": encoding["input_ids"].squeeze(0),
            "attention_mask": encoding["attention_mask"].squeeze(0),
            "token_type_ids": encoding["token_type_ids"].squeeze(0),
            "labels": torch.tensor(label_ids, dtype=torch.long),
        }


# ─────────────────────────────────────────────────────────
# 2. BiLSTM + CRF 模型
# ─────────────────────────────────────────────────────────
class BiLSTMCRF(nn.Module):
    """
    BiLSTM + CRF NER 模型

    架构：
        字符嵌入(Embedding) → BiLSTM → Dropout → 线性层 → CRF 解码
    """

    def __init__(
        self,
        vocab_size: int,
        embed_dim: int = 128,
        hidden_dim: int = 256,
        num_layers: int = 2,
        num_labels: int = len(NER_LABELS),
        dropout: float = 0.3,
    ):
        super().__init__()
        self.embedding = nn.Embedding(vocab_size, embed_dim, padding_idx=0)
        self.bilstm = nn.LSTM(
            embed_dim,
            hidden_dim // 2,
            num_layers=num_layers,
            batch_first=True,
            bidirectional=True,
            dropout=dropout if num_layers > 1 else 0,
        )
        self.dropout = nn.Dropout(dropout)
        self.fc = nn.Linear(hidden_dim, num_labels)
        self.crf = CRF(num_labels, batch_first=True)

    def forward(
        self,
        input_ids: torch.Tensor,          # (B, L)
        attention_mask: torch.Tensor,     # (B, L)
        labels: torch.Tensor = None,      # (B, L)  训练时传入
    ) -> Dict:
        x = self.embedding(input_ids)     # (B, L, embed_dim)
        x = self.dropout(x)
        x, _ = self.bilstm(x)            # (B, L, hidden_dim)
        x = self.dropout(x)
        emissions = self.fc(x)           # (B, L, num_labels)

        mask = attention_mask.bool()

        if labels is not None:
            # 训练：计算 CRF 负对数似然损失
            # CRF 要求 labels 中的 -100 用有效 id 替换（不影响 loss 计算，mask 已屏蔽）
            labels_crf = labels.clone()
            labels_crf[labels_crf == -100] = 0
            loss = -self.crf(emissions, labels_crf, mask=mask, reduction="mean")
            return {"loss": loss}
        else:
            # 推理：Viterbi 解码
            pred_ids = self.crf.decode(emissions, mask=mask)
            return {"predictions": pred_ids}


# ─────────────────────────────────────────────────────────
# 3. 训练函数
# ─────────────────────────────────────────────────────────
def train_bilstm_crf(
    train_samples: List[Dict],
    val_samples: List[Dict],
    vocab: Dict[str, int],
    epochs: int = 10,
    batch_size: int = 32,
    lr: float = 1e-3,
    save_path: str = "./models/bilstm_crf.pt",
) -> BiLSTMCRF:

    tokenizer = BertTokenizerFast.from_pretrained(BERT_MODEL_NAME)

    train_dataset = NERDataset(train_samples, tokenizer)
    val_dataset = NERDataset(val_samples, tokenizer)
    train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True)
    val_loader = DataLoader(val_dataset, batch_size=batch_size)

    model = BiLSTMCRF(vocab_size=tokenizer.vocab_size)
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.to(device)

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
            total_loss += loss.item()

        # ── 验证 ──
        f1 = evaluate_ner(model, val_loader, device)
        print(f"Epoch {epoch+1}/{epochs}  loss={total_loss/len(train_loader):.4f}  val_f1={f1:.4f}")

        if f1 > best_f1:
            best_f1 = f1
            torch.save(model.state_dict(), save_path)
            print(f"  ✓ 保存最优模型（f1={f1:.4f}）→ {save_path}")

    print(f"\n训练完成，最优 F1-score: {best_f1:.4f}")
    return model


# ─────────────────────────────────────────────────────────
# 4. 评估函数
# ─────────────────────────────────────────────────────────
def evaluate_ner(model, dataloader, device) -> float:
    model.eval()
    all_preds, all_labels = [], []
    with torch.no_grad():
        for batch in dataloader:
            labels = batch.pop("labels")
            batch = {k: v.to(device) for k, v in batch.items()}
            out = model(**batch)
            preds = out["predictions"]   # List[List[int]]

            for pred_seq, label_seq in zip(preds, labels):
                pred_tags, true_tags = [], []
                for p, l in zip(pred_seq, label_seq):
                    if l.item() == -100:
                        continue
                    pred_tags.append(NER_ID2LABEL[p])
                    true_tags.append(NER_ID2LABEL[l.item()])
                all_preds.append(pred_tags)
                all_labels.append(true_tags)

    return f1_score(all_labels, all_preds)


# ─────────────────────────────────────────────────────────
# 5. 推理接口
# ─────────────────────────────────────────────────────────
class NERPredictor:
    """加载训练好的 BiLSTM+CRF 模型，对单条文本进行 NER 推理"""

    def __init__(self, model_path: str, vocab_size: int = None):
        self.tokenizer = BertTokenizerFast.from_pretrained(BERT_MODEL_NAME)
        self.model = BiLSTMCRF(vocab_size=self.tokenizer.vocab_size)
        self.model.load_state_dict(torch.load(model_path, map_location="cpu"))
        self.model.eval()

    def predict(self, text: str) -> List[Tuple[str, str]]:
        """
        输入：文本字符串
        输出：[(字符, 标签), ...]
        """
        chars = list(text)
        encoding = self.tokenizer(
            chars,
            is_split_into_words=True,
            return_tensors="pt",
            max_length=128,
            truncation=True,
        )
        with torch.no_grad():
            out = self.model(
                input_ids=encoding["input_ids"],
                attention_mask=encoding["attention_mask"],
            )
        pred_ids = out["predictions"][0]

        # 对齐回原始字符
        word_ids = encoding.word_ids(batch_index=0)
        results = []
        seen = set()
        for token_idx, word_id in enumerate(word_ids):
            if word_id is None or word_id in seen:
                continue
            seen.add(word_id)
            if word_id < len(chars) and token_idx < len(pred_ids):
                results.append((chars[word_id], NER_ID2LABEL[pred_ids[token_idx]]))

        return results

    def extract_entities(self, text: str) -> Dict[str, List[str]]:
        """提取结构化实体字典"""
        tags = self.predict(text)
        entities: Dict[str, List[str]] = {}
        cur_entity, cur_type = [], None

        for char, label in tags:
            if label.startswith("B-"):
                if cur_entity:
                    etype = cur_type
                    entities.setdefault(etype, []).append("".join(cur_entity))
                cur_entity = [char]
                cur_type = label[2:]
            elif label.startswith("I-") and cur_type == label[2:]:
                cur_entity.append(char)
            else:
                if cur_entity:
                    entities.setdefault(cur_type, []).append("".join(cur_entity))
                cur_entity, cur_type = [], None

        if cur_entity:
            entities.setdefault(cur_type, []).append("".join(cur_entity))

        return entities


# ─────────────────────────────────────────────────────────
# Demo
# ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    # 构造模拟样本，演示数据结构
    sample_texts = [
        {
            "tokens": list("耐克AirMax运动鞋支持7天无理由退换"),
            "labels": [
                "B-BRAND", "I-BRAND",
                "B-PRODUCT", "I-PRODUCT", "I-PRODUCT", "I-PRODUCT", "I-PRODUCT",
                "B-PRODUCT", "I-PRODUCT", "I-PRODUCT",
                "O", "O",
                "B-DATE", "I-DATE",
                "O", "O", "O", "O",
            ],
        }
    ]
    print("BiLSTM+CRF NER 模型结构示例")
    model = BiLSTMCRF(vocab_size=21128)   # BERT 中文词表大小
    total_params = sum(p.numel() for p in model.parameters())
    print(f"  参数量：{total_params:,}")
    print(f"  NER 标签数：{len(NER_LABELS)}")
    print(f"  示例标签：{NER_LABELS}")
