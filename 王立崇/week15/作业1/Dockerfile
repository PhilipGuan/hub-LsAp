# 作业1：openai-agents 版本（情感识别 -> 鼓励/排解 四进程流水线）
#
# 构建：
#   docker build -t week15-agents:latest .
#   docker build --build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ -t week15-agents:latest .
#
# 运行（交互模式，需要 -it）：
#   docker run --rm -it --env-file .env week15-agents:latest
#
# 运行（单次输入）：
#   docker run --rm --env-file .env week15-agents:latest --text "最近压力好大"

FROM python:3.12-slim

ARG PIP_INDEX_URL=https://pypi.org/simple
ARG APP_VERSION=1.0.0
# 设为 1 时安装本地情感模型依赖（transformers + torch），镜像会明显变大
ARG WITH_LOCAL_MODEL=0

LABEL org.opencontainers.image.title="week15-emotion-agents" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.description="情感识别 -> 鼓励/排解，openai-agents 版四进程流水线"

# 容器内统一 UTF-8，日志不缓冲，便于 docker logs 实时看到各进程输出
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 先装依赖、后拷代码：requirements 没变时可直接复用缓存层
COPY requirements.agents.txt requirements.model.txt ./
RUN pip install --no-cache-dir --index-url "$PIP_INDEX_URL" -r requirements.agents.txt \
    && if [ "$WITH_LOCAL_MODEL" = "1" ]; then \
         pip install --no-cache-dir --index-url "$PIP_INDEX_URL" -r requirements.model.txt; \
       fi

# 只拷贝 Agents 版本需要的代码（LangGraph 版本不进镜像）
COPY shared ./shared
COPY agents_app ./agents_app
COPY run_agents.py ./

# 非 root 运行
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

# --web 模式监听端口
EXPOSE 8000

# 参数会原样透传给 run_agents.py，例如 --text "..."；
# 不带参数时进入交互模式（需要 docker run -it）
ENTRYPOINT ["python", "run_agents.py"]
