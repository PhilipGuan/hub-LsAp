# -*- coding: utf-8 -*-
"""全局配置。

所有可变配置都走环境变量，给默认值方便本地直接跑通。
"""

import os

from dotenv import load_dotenv

load_dotenv()  # 若存在 .env 文件（参考 .env.example），自动加载到环境变量

# ------- 目录 -------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")      # 原始上传的 PDF
PARSED_DIR = os.path.join(DATA_DIR, "parsed")        # MinerU 解析输出（markdown + images）
CHROMA_DIR = os.path.join(DATA_DIR, "chroma")        # 向量库持久化目录
SQLITE_PATH = os.path.join(DATA_DIR, "app.db")       # 元数据数据库

for _d in (DATA_DIR, UPLOAD_DIR, PARSED_DIR, CHROMA_DIR):
    os.makedirs(_d, exist_ok=True)

# ------- Kafka -------
KAFKA_BOOTSTRAP_SERVERS = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
KAFKA_TOPIC_PARSE_TASK = os.environ.get("KAFKA_TOPIC_PARSE_TASK", "pdf-parse-tasks")
KAFKA_GROUP_ID = os.environ.get("KAFKA_GROUP_ID", "pdf-parse-worker")

# ------- MinerU (magic-pdf CLI) -------
# magic-pdf 命令行工具名，若不在 PATH 里，可设置为绝对路径
MAGIC_PDF_CMD = os.environ.get("MAGIC_PDF_CMD", "magic-pdf")
# magic-pdf 解析方法：auto / txt / ocr，默认 auto 自动判断
MAGIC_PDF_METHOD = os.environ.get("MAGIC_PDF_METHOD", "auto")

# ------- 向量库 Embedding -------
# 使用 sentence-transformers 的多语言小模型做文本向量化，纯本地运行无需联网调用
EMBEDDING_MODEL_NAME = os.environ.get(
    "EMBEDDING_MODEL_NAME", "paraphrase-multilingual-MiniLM-L12-v2"
)

# ------- 多模态 LLM（OpenAI 兼容接口，默认 DashScope） -------
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
LLM_API_KEY = os.environ.get("LLM_API_KEY") or os.environ.get("DASHSCOPE_API_KEY", "")
LLM_MODEL_NAME = os.environ.get("LLM_MODEL_NAME", "qwen-vl-plus")

# ------- RAG -------
RAG_TOP_K = int(os.environ.get("RAG_TOP_K", "5"))
CHUNK_SIZE = int(os.environ.get("CHUNK_SIZE", "500"))
CHUNK_OVERLAP = int(os.environ.get("CHUNK_OVERLAP", "50"))

# ------- 文档状态常量 -------
STATUS_PENDING = "待解析"
STATUS_PARSING = "解析中"
STATUS_DONE = "解析完成"
STATUS_FAILED = "解析失败"
