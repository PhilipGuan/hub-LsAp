"""
=========================================================
BERT 中文情感 8 分类 —— 微调训练脚本
=========================================================
【任务类型】多分类监督学习（8 类情感分类）
【迁移学习范式】BERT-base-chinese 预训练 → 下游任务 Fine-tuning
【数据集】Simplified_Chinese_Multi-Emotion_Dialogue_Dataset.csv (4159 条)

【模块划分】
    模块0：配置区 & 可复现性（Seed 固定、设备自动检测）
    模块1：数据载入 + 标签编码（LabelEncoder 持久化映射）
    模块2：train/val/test 三分（70% / 15% / 15%，分层抽样）
    模块3：BERT 分词编码（WordPiece + Padding + Truncation）
    模块4：模型构建（8 类分类头 + id2label 可视化友好）
    模块5：评估指标函数（Accuracy + Macro-F1 + Weighted-F1）
    模块6：训练超参数（TrainingArguments，含 Early Stopping 思想）
    模块7：Trainer 训练循环 + 最佳 Checkpoint 恢复
    模块8：最终泛化评估 + 混淆矩阵输出

【运行方式】
    cd /Users/philipclaw/Downloads/padow-ai/Week4/Week04
    /Users/philipclaw/Downloads/padow-ai/.venv/bin/python 11_BERT情感分类_训练.py
"""

import os
import random
import numpy as np
import pandas as pd
import torch

# ===== 模块0：配置区 & 可复现性 =====
RANDOM_SEED = 42
DATASET_PATH = "/Users/philipclaw/Downloads/padow-ai/Week4/Week04/Simplified_Chinese_Multi-Emotion_Dialogue_Dataset.csv"
OUTPUT_DIR = "/Users/philipclaw/Downloads/padow-ai/Week4/Week04/emotion_bert_output"
LOGGING_DIR = os.path.join(OUTPUT_DIR, "logs")
MODEL_NAME = "google-bert/bert-base-chinese"   # HuggingFace 在线首次自动下载（420MB）
MAX_LENGTH = 64                                # 99% 文本 ≤ 62 字，此处覆盖 99.2% 样本

# ====== 日志后端自动检测 & 优雅降级 ======
# 修复 RuntimeError: TensorBoardCallback requires tensorboard to be installed
try:
    import tensorboard  # noqa: F401
    REPORT_TO = "tensorboard"   # 已装 tensorboard → 标准曲线可视化
except ImportError:
    REPORT_TO = "none"          # 未装 → 仅控制台日志，不崩溃

# ====== Seed 固定（保证实验可复现性）======
# 固定 4 个随机源：Python / NumPy / PyTorch CPU / PyTorch GPU
def set_seed(seed: int):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    # CuDNN 确定性模式（训练速度略慢，但完全可复现）
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False

set_seed(RANDOM_SEED)

# ====== 设备自动检测（Apple Silicon 优先 MPS）======
if torch.backends.mps.is_available():
    DEVICE = torch.device("mps")
    print(f"✅ 检测到 Apple Silicon GPU，使用 MPS 加速")
elif torch.cuda.is_available():
    DEVICE = torch.device("cuda")
    print(f"✅ 检测到 NVIDIA GPU，使用 CUDA 加速：{torch.cuda.get_device_name(0)}")
else:
    DEVICE = torch.device("cpu")
    print(f"⚠️  未检测到 GPU，使用 CPU 训练（速度较慢）")

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(LOGGING_DIR, exist_ok=True)

# ====== Transformers / Sklearn 库导入 ======
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder
from sklearn.metrics import classification_report, confusion_matrix
from transformers import (
    BertTokenizer,
    BertForSequenceClassification,
    Trainer,
    TrainingArguments,
    DataCollatorWithPadding,
)
from datasets import Dataset
import evaluate

# ===== 模块1：数据载入 + 标签工程 =====
print("\n" + "=" * 70)
print("📦 模块1：数据载入 + 标签编码")
df = pd.read_csv(DATASET_PATH)
print(f"   总样本数：{len(df)}")
print(f"   文本列：text，标签列：label")

# 文本标签 → 整数编码（8 类：0~7）
lbl_encoder = LabelEncoder()
y_integer = lbl_encoder.fit_transform(df["label"].values)  # y ∈ {0,1,...,7}
X_texts = df["text"].values.tolist()

# 保存双向映射，模块8 混淆矩阵可视化要用到
label_list = lbl_encoder.classes_.tolist()                 # ['伤心','关心','厌恶','平静','开心','惊讶','生气','疑问']
id2label = {i: lab for i, lab in enumerate(label_list)}
label2id = {lab: i for i, lab in enumerate(label_list)}
NUM_LABELS = len(label_list)
print(f"   类别数：{NUM_LABELS}")
print(f"   标签映射：{id2label}")
print(f"   标签分布：")
for lab, cnt in df["label"].value_counts().items():
    print(f"      {lab:<4}（id={label2id[lab]:>2}）: {cnt:>4} 条 ({cnt/len(df)*100:>5.1f}%)")

# ===== 模块2：数据集三分（Train / Val / Test = 70 / 15 / 15）=====
# 为什么三分而不是两分？
#   Train：更新模型权重（70%）
#   Val  ：选超参数、early stopping、选 best checkpoint（避免数据泄露！）
#   Test ：一次性最终评估，得到 unbiased 泛化性能估计（15%）
print("\n" + "=" * 70)
print("✂️  模块2：数据集三分（70 / 15 / 15，分层抽样）")

# Step 1: 先切 85% (trainval) + 15% (test)
X_trainval, X_test, y_trainval, y_test = train_test_split(
    X_texts, y_integer,
    test_size=0.15,
    stratify=y_integer,          # 保证每一步切分的类别比例都与整体一致
    random_state=RANDOM_SEED,
)
# Step 2: 再把 trainval 按 70/15 的比例切 → train ≈ 70%, val ≈ 15%
val_size_of_all = 0.15
val_size_of_trainval = val_size_of_all / 0.85   # ≈ 0.1765
X_train, X_val, y_train, y_val = train_test_split(
    X_trainval, y_trainval,
    test_size=val_size_of_trainval,
    stratify=y_trainval,
    random_state=RANDOM_SEED,
)
print(f"   Train 集: {len(X_train):>4} 条 ({len(X_train)/len(df)*100:.1f}%)")
print(f"   Val   集: {len(X_val):>4} 条 ({len(X_val)/len(df)*100:.1f}%) — 用于早停选最佳模型")
print(f"   Test  集: {len(X_test):>4} 条 ({len(X_test)/len(df)*100:.1f}%) — 仅最终评估一次")

# ===== 模块3：BERT 分词编码 =====
print("\n" + "=" * 70)
print("🔠 模块3：BERT WordPiece 分词与编码 (max_length=" + str(MAX_LENGTH) + ")")
tokenizer = BertTokenizer.from_pretrained(MODEL_NAME)

def tokenize_function(texts):
    """对一批文本进行统一编码：返回 input_ids / attention_mask / token_type_ids"""
    return tokenizer(
        texts,
        truncation=True,           # 超长截断至 MAX_LENGTH
        padding="max_length",      # 全部 pad 到 MAX_LENGTH（定长，便于静态图）
        max_length=MAX_LENGTH,
        return_tensors=None,       # 返回 Python list，交给 Dataset 处理
    )

# 对三折数据分别编码（注意：分词器是预训练好的，不包含 test 信息泄露风险）
train_enc = tokenize_function(X_train)
val_enc = tokenize_function(X_val)
test_enc = tokenize_function(X_test)

# 封装为 HuggingFace Dataset 对象（支持 shuffle / map / set_format）
def build_dataset(encodings, labels):
    data_dict = dict(encodings)           # input_ids, attention_mask, token_type_ids
    data_dict["labels"] = labels.tolist() if hasattr(labels, 'tolist') else list(labels)
    return Dataset.from_dict(data_dict)

train_dataset = build_dataset(train_enc, y_train)
val_dataset = build_dataset(val_enc, y_val)
test_dataset = build_dataset(test_enc, y_test)
print(f"   Train Dataset 特征列：{train_dataset.column_names}")
print(f"   单条样本 input_ids 长度：{len(train_dataset[0]['input_ids'])}")

# ===== 模块4：模型构建 =====
print("\n" + "=" * 70)
print("🧠 模块4：加载 BERT + 8 类分类头")
model = BertForSequenceClassification.from_pretrained(
    MODEL_NAME,
    num_labels=NUM_LABELS,
    id2label=id2label,          # 让模型内部知道 id → 字符串标签（日志/混淆更友好）
    label2id=label2id,
)
model.to(DEVICE)
total_params = sum(p.numel() for p in model.parameters())
trainable_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
print(f"   总参数量：{total_params/1e6:.2f} M")
print(f"   可训练参数量：{trainable_params/1e6:.2f} M")
print(f"   模型结构：[Input] → 12 层 BERT Encoder → <[BOS_never_used_51bce0c785ca2f68081bfa7d91973934]> 768 维 → Linear(768, {NUM_LABELS}) → logits")

# ===== 模块5：评估指标函数 =====
# 使用 HuggingFace evaluate 库加载标准指标（实现与 scikit-learn 一致但集成更好）
acc_metric = evaluate.load("accuracy")
f1_metric = evaluate.load("f1")

def compute_metrics(eval_pred):
    """
    eval_pred = (logits, labels)
        logits shape: [batch_size, NUM_LABELS]  (未归一化分数)
        labels shape: [batch_size]              (整数标签)
    """
    logits, labels = eval_pred
    predictions = np.argmax(logits, axis=-1)   # 取 logit 最大值 → 预测类别

    accuracy = acc_metric.compute(predictions=predictions, references=labels)["accuracy"]
    macro_f1 = f1_metric.compute(
        predictions=predictions, references=labels, average="macro"
    )["f1"]                                     # 各类 F1 算术平均（对小类更敏感）
    weighted_f1 = f1_metric.compute(
        predictions=predictions, references=labels, average="weighted"
    )["f1"]                                     # 按样本数加权平均（更贴近 Accuracy）
    return {
        "accuracy": accuracy,
        "macro_f1": macro_f1,
        "weighted_f1": weighted_f1,
    }

# ===== 模块6：训练超参数配置 =====
print("\n" + "=" * 70)
print("⚙️  模块6：配置 TrainingArguments")

total_train_samples = len(train_dataset)
batch_size = 16
NUM_EPOCHS = 4
steps_per_epoch = (total_train_samples + batch_size - 1) // batch_size   # ceil 除法
total_steps = steps_per_epoch * NUM_EPOCHS

# transformers 5.x 已移除 warmup_ratio，改为手动计算 warmup_steps
WARMUP_RATIO = 0.1
warmup_steps = int(total_steps * WARMUP_RATIO)

training_args = TrainingArguments(
    # ===== 输出与日志 =====
    output_dir=OUTPUT_DIR,
    # transformers 5.x 已移除 logging_dir 参数；日志默认写到 output_dir/runs/*
    logging_steps=20,                 # 每 20 step 打印一次 loss / lr
    logging_first_step=True,          # 第 0 步也记录，方便看初始 loss
    run_name="emotion8_bert_base_chinese_seed42",  # 便于 TensorBoard 多实验对比

    # ===== 训练规模（核心超参数）=====
    num_train_epochs=NUM_EPOCHS,      # 微调 BERT 通常 2~5 epoch，情感分类 3~4 最佳
    per_device_train_batch_size=batch_size,   # 每个设备 mini-batch
    per_device_eval_batch_size=32,            # 评估不需要梯度，batch 可以更大
    learning_rate=2e-5,                       # 🔥 BERT 微调黄金学习率：1e-5 ~ 5e-5
                                              # 太小→欠拟合太慢，太大→灾难性遗忘预训练权重

    # ===== 优化器与正则化 =====
    warmup_steps=warmup_steps,        # 总步数前 10% 线性预热 LR
                                      # 训练初期梯度方差大，低 LR 防震荡
    weight_decay=0.01,                        # AdamW 的 L2 正则系数，抑制大权重 → 防过拟合
    adam_beta1=0.9, adam_beta2=0.999, adam_epsilon=1e-8,  # AdamW 默认参数
    max_grad_norm=1.0,                        # 梯度裁剪阈值：抑制梯度爆炸

    # ===== 评估与保存策略（Early Stopping 核心）=====
    eval_strategy="steps",                    # 按 step 评估（比 epoch 更细粒度）
    eval_steps=steps_per_epoch // 2,          # 每个 epoch 评估 2 次
    save_strategy="steps",                    # 与 eval_strategy 同步
    save_steps=steps_per_epoch // 2,
    save_total_limit=3,                       # 只保留最近 3 个 checkpoint，节省磁盘

    load_best_model_at_end=True,              # 🔥 训练结束后自动恢复「验证集最优」的 checkpoint
    metric_for_best_model="eval_macro_f1",    # 以 Macro-F1 为最佳模型的评选标准（比 accuracy 更公平）
    greater_is_better=True,                   # F1 越大越好

    # ===== 运行效率 =====
    fp16=False,                               # 半精度：需要 NVIDIA GPU，MPS 暂不支持
    dataloader_num_workers=0,                 # DataLoader 多线程（macOS 上 0 更稳定）
    # MPS (Apple Silicon GPU) 不支持 CUDA pinned memory，显式关闭避免 UserWarning
    dataloader_pin_memory=False if torch.backends.mps.is_available() else True,
    # transformers 5.x 已移除 group_by_length，保留定长 padding 即可

    # ===== 可复现性 =====
    seed=RANDOM_SEED,
    data_seed=RANDOM_SEED,
    report_to=REPORT_TO,                       # "tensorboard" 或 "none"（自动降级）

    # ===== 防止意外崩溃 =====
    remove_unused_columns=True,               # 自动去掉 Dataset 中模型不需要的列
)
print(f"   总训练步数：{total_steps}（{steps_per_epoch} step/epoch × {NUM_EPOCHS} epoch）")
print(f"   Warmup 步数 ：{warmup_steps}（前 {WARMUP_RATIO*100:.0f}%）")
print(f"   评估频率：每 {training_args.eval_steps} step 一次（每 epoch 2 次）")
print(f"   最佳模型评选标准：eval_macro_f1 最大的 checkpoint")

# ===== 模块7：Trainer 训练 =====
print("\n" + "=" * 70)
print("🚀 模块7：启动 Trainer 训练循环")
data_collator = DataCollatorWithPadding(tokenizer=tokenizer)   # 动态 padding（配合 group_by_length）

trainer = Trainer(
    model=model,
    args=training_args,
    train_dataset=train_dataset,
    eval_dataset=val_dataset,
    compute_metrics=compute_metrics,
    data_collator=data_collator,
    # 默认优化器：AdamW（weight_decay 已分离）+ 线性学习率衰减
)

print("   📉 开始训练（4 epoch，进度条下方显示）…")
train_result = trainer.train()
print("\n   ✅ 训练完成，已自动加载验证集 Macro-F1 最优的 checkpoint")

# ===== 模块8：最终泛化评估（仅在 Test 集跑一次）=====
print("\n" + "=" * 70)
print("🧪 模块8：最终泛化评估（Test 集，仅运行一次）")

# 8.1 标准 Trainer.evaluate 指标
final_test_eval = trainer.evaluate(eval_dataset=test_dataset)
print("\n【Test 集核心指标】")
for metric in ["eval_accuracy", "eval_macro_f1", "eval_weighted_f1"]:
    if metric in final_test_eval:
        print(f"   {metric:<20} = {final_test_eval[metric]:.4f}")

# 8.2 生成预测结果 → 详细分类报告 + 混淆矩阵
print("\n【获得 Test 集预测分布】")
predict_output = trainer.predict(test_dataset)
logits = predict_output.predictions
y_true = predict_output.label_ids
y_pred = np.argmax(logits, axis=-1)

print("\n📋 分类报告（per-class Precision / Recall / F1）：")
print(classification_report(
    y_true, y_pred,
    labels=list(range(NUM_LABELS)),
    target_names=label_list,
    digits=4,
))

print("\n🔲 混淆矩阵（行=真实，列=预测）：")
cm = confusion_matrix(y_true, y_pred, labels=list(range(NUM_LABELS)))
# 美化输出：行/列都带标签名；注意 '真实 \\ 预测' 中反斜杠用 \\ 转义，或使用空格分隔
col_header = "真实 / 预测"
print(f"{col_header:>10}", end="")
for lab in label_list:
    print(f"{lab:>8}", end="")
print()
for i, lab in enumerate(label_list):
    print(f"{lab:>10}", end="")
    for j in range(NUM_LABELS):
        print(f"{cm[i][j]:>8}", end="")
    print()

# 保存结果到文件
results_path = os.path.join(OUTPUT_DIR, "final_test_results.txt")
with open(results_path, "w", encoding="utf-8") as f:
    f.write("=" * 70 + "\n")
    f.write("BERT 情感分类最终 Test 集结果\n")
    f.write("=" * 70 + "\n")
    f.write(f"模型：{MODEL_NAME}\n")
    f.write(f"Train / Val / Test = {len(X_train)} / {len(X_val)} / {len(X_test)}\n")
    f.write(f"随机种子：{RANDOM_SEED}\n\n")
    f.write("【核心指标】\n")
    for metric in ["eval_accuracy", "eval_macro_f1", "eval_weighted_f1"]:
        if metric in final_test_eval:
            f.write(f"  {metric:<20} = {final_test_eval[metric]:.4f}\n")
    f.write("\n【分类报告】\n")
    f.write(classification_report(
        y_true, y_pred,
        labels=list(range(NUM_LABELS)),
        target_names=label_list,
        digits=4,
    ))
    f.write("\n【混淆矩阵】\n")
    header = f"{col_header:>10}" + "".join(f"{lab:>8}" for lab in label_list) + "\n"
    f.write(header)
    for i, lab in enumerate(label_list):
        row = f"{lab:>10}" + "".join(f"{cm[i][j]:>8}" for j in range(NUM_LABELS)) + "\n"
        f.write(row)

print(f"\n💾 结果已持久化保存至：{results_path}")
print(f"💾 最佳模型 checkpoint 位于：{OUTPUT_DIR} （可直接 trainer.predict / pipeline 调用）")

# ===== 训练历史保存 =====
history_path = os.path.join(OUTPUT_DIR, "training_history.csv")
log_history = trainer.state.log_history
if log_history:
    hist_df = pd.DataFrame(log_history)
    hist_df.to_csv(history_path, index=False, encoding="utf-8")
    print(f"💾 训练指标历史（loss/lr/F1 曲线）：{history_path}")

print("\n" + "=" * 70)
print("🎉 训练脚本执行完毕！")
print("=" * 70)
print("📌 下一步建议：")
print("   1. 查看分类报告中 F1 较低的类别，分析是否需要增加该类样本 / class weights")
print("   2. 打开 TensorBoard 看 loss / macro_f1 曲线：是否还能继续训？是否过拟合？")
print("      命令：.venv/bin/tensorboard --logdir " + LOGGING_DIR)
print("   3. 用 trainer.predict() 做单条 / 批量推理；或 pipeline('text-classification', model=best_dir)")
