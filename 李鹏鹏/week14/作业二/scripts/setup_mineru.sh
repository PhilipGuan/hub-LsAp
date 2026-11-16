#!/usr/bin/env bash
# 安装 MinerU（经典 2.x/3.x CLI，支持 -p/-o/-b 参数）
# 说明：模型约 1GB+，首次解析会自动下载；也可以用 MINERU_API_URL 接远程服务。
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON_BIN="${PYTHON:-python3}"
echo "==> 使用 Python：$PYTHON_BIN"
"$PYTHON_BIN" -m pip install -U "mineru[core]"

echo "==> 预下载模型（失败不影响，首次解析时会自动重试）"
"$PYTHON_BIN" -m mineru.cli.models_download -s modelscope -m pipeline || \
  mineru-models-download -s modelscope -m pipeline || true

echo "==> 检查命令"
mineru --version || true
echo
echo "完成。把 .env 中的 PARSER_BACKEND 设为 mineru 或保持 auto 即可。"
