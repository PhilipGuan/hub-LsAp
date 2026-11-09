"""配置加载：从环境变量 / .env 读取 DeepSeek 多模态接口参数。

所有可调参数都走环境变量，改 .env 即可，无需改代码。
"""
import os

from dotenv import load_dotenv

# 加载 backend/.env（若存在）
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

# DeepSeek 多模态接口（OpenAI 兼容）
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
# DeepSeek-V4.1-Flash-1017 的官方模型 ID，支持视觉输入
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-flash")

# 单张图片大小上限（字节），超限拒绝，避免拖垮接口。DeepSeek 视觉支持单图 ≤ 32MiB。
MAX_IMAGE_BYTES = int(os.getenv("MAX_IMAGE_BYTES", 15 * 1024 * 1024))

# 服务监听地址与端口
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))
