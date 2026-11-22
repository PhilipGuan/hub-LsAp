# 单镜像方案：构建 Vue 前端，并由 FastAPI 同端口托管前端 + API。
# 两个 worker 复用同一镜像，启动时覆盖 CMD 即可。
# 构建：docker build -t sentiment-chat-agents .
# 运行：见 docker-compose.yml 或 README 说明

# ---------- 阶段 1：构建前端静态文件 ----------
FROM node:20-slim AS frontend-build

WORKDIR /web

# 先复制依赖清单，依赖不变时可复用 npm 缓存层
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

# ---------- 阶段 2：Python 运行时 ----------
FROM python:3.12-slim

# 不生成 .pyc；日志实时输出，避免容器里看不到 print / logging
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 先只复制依赖清单，依赖不变时可复用缓存层
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 只复制运行时代码；.env 不进入镜像，运行时用 --env-file / compose env_file 传入
COPY shared ./shared
COPY backend ./backend

# 前端产物放在 backend/main.py 约定的路径下，存在即由 FastAPI 托管
COPY --from=frontend-build /web/dist ./frontend/dist

EXPOSE 8000

# 默认启动 FastAPI（同时提供 / 前端页面）；worker 通过覆盖 CMD 启动，例如：
#   docker run ... <image> python -m backend.agents_sdk_impl.sentiment_worker
CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
