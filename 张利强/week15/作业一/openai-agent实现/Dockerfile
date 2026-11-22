# 单镜像承载三个进程（sentiment / response / api），通过启动命令区分：
#   python -m backend.sentiment_service
#   python -m backend.response_service
#   uvicorn backend.api:app --host 0.0.0.0 --port 8000
FROM python:3.12-slim

# 不生成 .pyc、日志实时输出，便于 docker logs 观察
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 先只拷贝依赖清单，代码变动时仍能命中依赖层缓存
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 业务代码；.env 不打进镜像（由 docker-compose 的 env_file / -e 注入）
COPY backend ./backend
COPY frontend ./frontend

# 非 root 运行，降低容器逃逸风险
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# 默认启动 API；其余两个进程在 docker-compose 中覆盖 command
CMD ["uvicorn", "backend.api:app", "--host", "0.0.0.0", "--port", "8000"]
