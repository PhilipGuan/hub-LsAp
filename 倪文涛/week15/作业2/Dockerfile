# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    KAFKA_REQUEST_TOPIC=emotion-requests \
    KAFKA_CLASSIFICATION_TOPIC=emotion-classifications \
    KAFKA_RESPONSE_TOPIC=emotion-responses \
    KAFKA_CLASSIFIER_GROUP=emotion-classifier-group \
    KAFKA_RESPONDER_GROUP=emotion-responder-group \
    KAFKA_API_GROUP=emotion-api-group \
    LLM_TIMEOUT_SECONDS=300 \
    BACKEND_URL=http://127.0.0.1:8001

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install -r requirements.txt

COPY backend/ ./backend/
COPY frontend/ ./frontend/

RUN useradd --create-home --uid 10001 app
USER app

EXPOSE 8001 8501
STOPSIGNAL SIGTERM

# Start all four services and keep the container running.
CMD ["/bin/bash", "-c", "python -m uvicorn backend.main:app --host 0.0.0.0 --port 8001 --workers 1 & \
    python -m backend.classifier_main & \
    python -m backend.responder_main & \
    python -m streamlit run frontend/app.py --server.address=0.0.0.0 --server.port=8501 --server.headless=true --browser.gatherUsageStats=false & \
    wait"]
