"""配置：模型、后端地址、图片限制。"""
import os

# 百炼 / DashScope（OpenAI 兼容接口）
os.environ.setdefault("OPENAI_API_KEY", "sk-你的百炼Key")
os.environ.setdefault(
    "OPENAI_BASE_URL",
    "https://dashscope.aliyuncs.com/compatible-mode/v1",
)

OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
OPENAI_BASE_URL = os.environ["OPENAI_BASE_URL"]
MODEL_NAME = os.environ.get("VISION_MODEL", "qwen-vl-max")

# 图片限制
MAX_IMAGE_MB = 10          # 上传大小上限
MAX_IMAGE_SIDE = 1280      # 压缩后长边
JPEG_QUALITY = 80          # 压缩质量
REQUEST_TIMEOUT = 30       # 模型调用超时（秒）

# 前端调用后端
BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000")