# 作业2：基于作业1 的 openai-agents 版本打包镜像
FROM python:3.11-slim

# 避免交互式安装、保证日志即时输出
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 先装依赖（利用 Docker 层缓存）
COPY requirements.txt ./
RUN pip install -r requirements.txt

# 再复制源码
COPY app ./app
COPY frontend ./frontend

# 默认使用 openai-agents 实现
ENV HOST=0.0.0.0 \
    PORT=8000 \
    DEFAULT_IMPL=openai-agents

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
