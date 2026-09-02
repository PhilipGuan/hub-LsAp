"""
全局配置文件
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ── Deepseek API ──────────────────────────────────────────
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "your_deepseek_api_key")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# ── Neo4j ─────────────────────────────────────────────────
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")

# ── Redis（对话会话缓存）──────────────────────────────────
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("REDIS_DB", "0"))
SESSION_TTL = 3600  # 会话过期时间（秒）

# ── 模型路径 ──────────────────────────────────────────────
BERT_MODEL_NAME = os.getenv("BERT_MODEL_NAME", "hfl/chinese-roberta-wwm-ext")
NER_MODEL_PATH = os.getenv("NER_MODEL_PATH", "./models/ner")
INTENT_MODEL_PATH = os.getenv("INTENT_MODEL_PATH", "./models/intent")
EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL_NAME", "BAAI/bge-small-zh-v1.5")

# ── 检索参数 ──────────────────────────────────────────────
BM25_TOP_K = 10
VECTOR_TOP_K = 10
FINAL_TOP_K = 5
RRF_K = 60          # RRF 融合常数

# ── 意图类别定义 ──────────────────────────────────────────
INTENT_LABELS = {
    0: "INT_001_物流查询",
    1: "INT_002_退换货咨询",
    2: "INT_003_商品参数查询",
    3: "INT_004_优惠活动查询",
    4: "INT_005_投诉建议",
    5: "INT_006_其他",
}

# ── NER 标签定义（BIO 格式）───────────────────────────────
NER_LABELS = [
    "O",
    "B-PRODUCT", "I-PRODUCT",   # 商品名
    "B-BRAND",   "I-BRAND",     # 品牌名
    "B-ATTR",    "I-ATTR",      # 属性词
    "B-PRICE",   "I-PRICE",     # 价格
    "B-DATE",    "I-DATE",      # 日期
    "B-ORDER",   "I-ORDER",     # 订单号
]
NER_LABEL2ID = {label: i for i, label in enumerate(NER_LABELS)}
NER_ID2LABEL = {i: label for i, label in enumerate(NER_LABELS)}
