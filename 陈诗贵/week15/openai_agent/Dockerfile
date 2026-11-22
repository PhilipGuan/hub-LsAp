# syntax=docker/dockerfile:1
# Week15 作业1 · 情绪疏导系统（OpenAI Agents SDK 版）
# 单容器内由 run_all.py 拉起四个进程：5012 情感识别 / 5013 共情回复 / 5010 后端 / 5011 前端
# 只对外发布 5011（前端）；其余端口仅在容器内部通信。
FROM python:3.12-slim

# 国内网络默认走腾讯云镜像（同机房更快）；海外机器可用 --build-arg 覆盖
ARG PIP_INDEX_URL=https://mirrors.cloud.tencent.com/pypi/simple
ARG PIP_TRUSTED_HOST=mirrors.cloud.tencent.com

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    BIND_HOST=0.0.0.0

WORKDIR /app

# 依赖单独一层，改代码时不必重装
# --timeout/--retries：大包下载慢时避免 ReadTimeoutError
COPY requirements.txt ./
RUN pip install --no-cache-dir --prefer-binary --timeout 120 --retries 5 \
        -i "${PIP_INDEX_URL}" --trusted-host "${PIP_TRUSTED_HOST}" \
        -r requirements.txt

# 显式拷贝业务文件：.env / README / 缓存一律不进镜像
COPY common.py sentiment_service.py reply_service.py backend_service.py frontend_service.py run_all.py ./
COPY static ./static
COPY tests ./tests

# 非 root 运行
RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 5011

# 探活：前端 /health（四进程由 run_all 启动，故 start-period 放宽）
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5011/health', timeout=3)"]

CMD ["python", "-u", "run_all.py"]
