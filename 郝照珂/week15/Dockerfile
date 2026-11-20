FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements-agents.txt .
RUN python -m pip install --no-cache-dir -r requirements-agents.txt
COPY sentiment/ ./sentiment/

USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
    CMD ["python", "-c", "from urllib.request import urlopen; urlopen('http://127.0.0.1:8000/health', timeout=2).close()"]
CMD ["python", "-m", "uvicorn", "sentiment.server:create_agents_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
