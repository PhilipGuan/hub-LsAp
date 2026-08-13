"""全局配置：意图类别、模型路径、训练参数。

所有模块统一从此处读取配置，避免散落的魔法值。
"""

import os

# ---------------------------------------------------------------------------
# 意图类别体系（顺序即标签 ID，与产品文档保持一致）
# ---------------------------------------------------------------------------
CATEGORY_NAME = [
    'Product-Query',    # 0 商品查询
    'Price-Query',      # 1 价格咨询
    'Order-Query',      # 2 订单查询
    'Logistics-Query',  # 3 物流查询
    'Return-Refund',    # 4 退换货
    'After-Sale',       # 5 售后保修
    'Payment',          # 6 支付问题
    'Promotion',        # 7 优惠活动
    'Human-Service',    # 8 人工客服
    'Other',            # 9 其他
]

# label -> id 的映射，供训练脚本使用
CATEGORY_ID = {name: idx for idx, name in enumerate(CATEGORY_NAME)}

# ---------------------------------------------------------------------------
# 数据与模型路径
# ---------------------------------------------------------------------------
DATASET_PATH = "assets/dataset/ecommerce_intent.csv"
TFIDF_MODEL_PKL_PATH = "assets/weights/tfidf_ml.pkl"
BERT_MODEL_PKL_PATH = "assets/weights/bert.pt"
BERT_MODEL_PERTRAINED_PATH = "bert-base-chinese"
BERT_OUTPUT_DIR = "assets/weights/bert/"

# 停用词：优先使用本地文件，缺失时回退到远程地址
LOCAL_STOPWORDS_PATH = "assets/dataset/baidu_stopwords.txt"
REMOTE_STOPWORDS_URL = "http://mirror.coggle.club/stopwords/baidu_stopwords.txt"

# ---------------------------------------------------------------------------
# 训练参数
# ---------------------------------------------------------------------------
RANDOM_SEED = 42
TEST_SIZE = 0.2            # 验证集比例
BERT_MAX_LENGTH = 64       # BERT 最大序列长度
BERT_NUM_EPOCHS = 6        # 训练轮数
BERT_BATCH_SIZE = 16

# 若使用 Hugging Face 镜像加速下载，取消注释下一行
# os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
