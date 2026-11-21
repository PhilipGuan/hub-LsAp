# 作业2 · 打包 OpenAI-Agents 版后端（不含 LangGraph / 前端）
FROM python:3.11-slim

# 避免日志缓冲，实时输出
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 依赖单独一层：代码改动时不重装依赖（利用 Docker 构建缓存）
COPY docker-requirements.txt .
RUN pip install --no-cache-dir -r docker-requirements.txt \
    -i https://pypi.tuna.tsinghua.edu.cn/simple

# 只拷贝 agents 后端运行所需代码（langgraph_flow.py 已由 .dockerignore 排除）
COPY core/ core/
COPY storage/ storage/
COPY flows/ flows/
COPY api/ api/
COPY prompts/ prompts/

# SQLite 数据库默认写 /app/app.db，运行时可挂载卷持久化
EXPOSE 8002

CMD ["uvicorn", "api.agents_server:app", "--host", "0.0.0.0", "--port", "8002"]
