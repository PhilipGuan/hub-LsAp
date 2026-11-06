from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
DATABASE_URL = "sqlite+pysqlite:///chinook.db"
LLM_API_KEY = "sk-xxx"  # 在本地填写自己的密钥。
LLM_BASE_URL = "https://api.siliconflow.cn/v1"
LLM_MODEL = "deepseek-ai/DeepSeek-V3"
LLM_ENABLE_THINKING = False
