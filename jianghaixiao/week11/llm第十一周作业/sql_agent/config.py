"""全局配置：模型、数据库、重试参数。"""
import os

# 百炼 / DashScope（OpenAI 兼容接口）
os.environ.setdefault("OPENAI_API_KEY", "sk-24fdaa8f9b1c433d889cb496bc85e532")
os.environ.setdefault("OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")

OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
OPENAI_BASE_URL = os.environ["OPENAI_BASE_URL"]
MODEL_NAME = os.environ.get("OPENAI_MODEL", "qwen-max")

# 数据库（相对路径：必须在 sql_agent 目录下运行）
DB_URL = os.environ.get("CHATBI_DB_URL", "sqlite:///./chinook.db")

MAX_RETRIES = 3      # SQL 改写最多重试次数
MAX_ROWS = 100       # 单次查询最多返回行数
MAX_PREVIEW = 20     # 汇总时最多给模型看的行数