"""配置：模型、数据库、重试参数。"""
import os

os.environ.setdefault("OPENAI_API_KEY", "sk-你的百炼Key")
os.environ.setdefault(
    "OPENAI_BASE_URL",
    "https://dashscope.aliyuncs.com/compatible-mode/v1",
)

OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
OPENAI_BASE_URL = os.environ["OPENAI_BASE_URL"]
MODEL_NAME = os.environ.get("OPENAI_MODEL", "qwen-max")

DB_URL = os.environ.get("CHATBI_DB_URL", "sqlite:///./chinook.db")

MAX_RETRIES = 3
MAX_ROWS = 100
MAX_PREVIEW = 20