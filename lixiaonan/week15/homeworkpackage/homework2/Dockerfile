# ============================================================================
# 作业2：情感陪伴助手 —— openai-agents 版本 Docker 镜像
#
# 构建：  docker build -t sentiment-companion:agents .
# 运行：  docker run --rm -p 8100:8100 \
#           -e LLM_API_KEY=<你的Key> \
#           -e LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4/ \
#           -e LLM_MODEL=glm-5.3-flash \
#           sentiment-companion:agents
#
# 安全原则：API Key 等密钥绝不写入镜像（.dockerignore 已排除 .env），
#           统一通过运行时环境变量注入（-e 或 --env-file）。
# ============================================================================

FROM python:3.12-slim

# pip 源：默认阿里云镜像（国内构建提速）
# 如需官方源构建：docker build --build-arg PIP_INDEX_URL=https://pypi.org/simple .
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/

WORKDIR /app

# 先装依赖、再拷代码：依赖层可被缓存，代码改动不触发重装依赖
COPY app/requirements.txt .
RUN pip install --no-cache-dir -i ${PIP_INDEX_URL} -r requirements.txt

# 拷贝应用代码（openai-agents 版本：不含 langgraph_impl，镜像内无 langchain 依赖）
COPY app/ .

# 运行期默认值：镜像固定为 openai-agents 引擎（作业要求）；密钥不在 ENV 中
ENV PIPELINE_ENGINE=openai-agents \
    PORT=8100

EXPOSE 8100

# 以非 root 用户运行（最小权限原则）
RUN useradd --create-home appuser
USER appuser

# 健康检查：探测 /api/health，端口跟随 $PORT
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import os,sys,urllib.request as req; sys.exit(0 if req.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('PORT','8100'), timeout=4).getcode()==200 else 1)"

# shell 形式 CMD：端口跟随 ENV PORT（覆盖 PORT 时无需改镜像）
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8100}
