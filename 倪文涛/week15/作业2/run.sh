#!/usr/bin/env bash
set -e

# 以下为参考值，已 export 的同名环境变量优先；API 密钥占位值需替换。
exec docker run -d \
  --name emotion-chat \
  --init \
  --network emotion-net \
  -p 8501:8501 \
  -p 8001:8001 \
  -e KAFKA_BOOTSTRAP_SERVERS="127.0.0.1:9092" \
  -e LLM_BASE_URL="https://api.siliconflow.cn/v1" \
  -e LLM_MODEL="Qwen/Qwen3.5-27B" \
  -e LLM_API_KEY="sk-xxx" \
  emotion-analysis-openaiagents:local
