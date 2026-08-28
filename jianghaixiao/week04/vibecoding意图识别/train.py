"""
训练脚本 - 训练 TFIDF + LinearSVC 意图分类模型

用法:
    python train.py          # 在项目根目录运行
"""

import os, pandas as pd, jieba
from joblib import dump
from sklearn.svm import LinearSVC
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, accuracy_score

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
WEIGHTS_DIR = os.path.join(BASE_DIR, "weights")
os.makedirs(WEIGHTS_DIR, exist_ok=True)

# ---- 1. 加载数据 ----------------------------------------------------
print("[1/5] 加载数据...")
train_data = pd.read_csv(
    os.path.join(DATA_DIR, "dataset.csv"), sep="\t", header=None
)
cn_stopwords = pd.read_csv(
    os.path.join(DATA_DIR, "baidu_stopwords.txt"), header=None
)[0].values

# ---- 2. 分词 + 去停用词 ---------------------------------------------
print("[2/5] 分词...")
train_data[0] = train_data[0].apply(
    lambda x: " ".join([w for w in jieba.lcut(x) if w not in cn_stopwords])
)

# ---- 3. TFIDF 向量化 ------------------------------------------------
print("[3/5] TFIDF 向量化...")
tfidf = TfidfVectorizer(ngram_range=(1, 2))
X = tfidf.fit_transform(train_data[0])
y = train_data[1]

# ---- 4. 训练 --------------------------------------------------------
print("[4/5] 训练 LinearSVC...")
X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, random_state=42, stratify=y
)
model = LinearSVC(max_iter=10000)
model.fit(X_train, y_train)

# ---- 5. 评估与保存 --------------------------------------------------
print("[5/5] 评估与保存...")
pred = model.predict(X_test)
print(f"Accuracy: {accuracy_score(y_test, pred):.4f}")
print(classification_report(y_test, pred, zero_division=0))

pkl_path = os.path.join(WEIGHTS_DIR, "tfidf_ml.pkl")
dump((tfidf, model), pkl_path)
print(f"\nSaved to: {pkl_path}")
