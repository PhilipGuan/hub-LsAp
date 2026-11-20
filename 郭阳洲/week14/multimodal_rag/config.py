import os

# ---------- Redis ----------
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("REDIS_DB", "0"))

# ---------- Kafka ----------
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "localhost:9092")
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "pdf-parse-tasks")

# ---------- Elasticsearch ----------
ES_URL = os.getenv("ES_URL", "http://localhost:9200")
ES_INDEX = os.getenv("ES_INDEX", "pdf_docs")

# ---------- 多模态 LLM（DeepSeek，OpenAI 兼容，支持图片输入）----------
VLM_BASE_URL = os.getenv("VLM_BASE_URL", "https://api.deepseek.com")
VLM_API_KEY = os.getenv("VLM_API_KEY", "sk-dba274e5d2topabu8c8f13b30c3205fdee")
VLM_MODEL = os.getenv("VLM_MODEL", "deepseek-flash")

# ---------- Embedding（本地 sentence-transformers 模型，不依赖外部 API）----------
EMBED_MODEL_NAME = os.getenv("EMBED_MODEL_NAME", "BAAI/bge-small-zh-v1.5")
EMBED_DIM = int(os.getenv("EMBED_DIM", "512"))

# ---------- 文件存储 ----------
DATA_DIR = os.getenv("DATA_DIR", "/workspace/data")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")   # 上传的原始 PDF
PARSE_DIR = os.path.join(DATA_DIR, "parsed")     # mineru 解析产物

for d in (DATA_DIR, UPLOAD_DIR, PARSE_DIR):
    os.makedirs(d, exist_ok=True)
