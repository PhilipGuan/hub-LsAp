#!/usr/bin/env bash
set -euo pipefail

# AutoDL 推荐从 PyTorch 2.x / Python 3.11 / CUDA 12.x 的 Ubuntu 22.04
# 基础镜像创建实例。本脚本只写系统盘，便于关机后直接“保存镜像”。
ENV_NAME="pageindex"
PYTHON_VERSION="3.11"
ROOT_DIR="/root/pageindex_homework"

mkdir -p "${ROOT_DIR}"
cp -a "$(cd "$(dirname "$0")" && pwd)/." "${ROOT_DIR}/"

if command -v conda >/dev/null 2>&1; then
  eval "$(conda shell.bash hook)"
  conda create -y -n "${ENV_NAME}" "python=${PYTHON_VERSION}"
  conda activate "${ENV_NAME}"
else
  python3 -m venv "${ROOT_DIR}/.venv"
  source "${ROOT_DIR}/.venv/bin/activate"
fi

python -m pip install --upgrade pip
python -m pip install -r "${ROOT_DIR}/requirements.txt"
python -c "from pageindex import PageIndexClient; print('PageIndex import: OK')"

# 可选：让 AutoDL GPU 实例也使用完全本地的 LLM。
# 用法：INSTALL_OLLAMA=1 OLLAMA_MODEL=qwen3:8b bash setup_autodl.sh
if [[ "${INSTALL_OLLAMA:-0}" == "1" ]]; then
  curl -fsSL https://ollama.com/install.sh | sh
  mkdir -p "${ROOT_DIR}/logs"
  nohup ollama serve >"${ROOT_DIR}/logs/ollama.log" 2>&1 &
  sleep 5
  ollama pull "${OLLAMA_MODEL:-qwen3:8b}"
fi

cat <<'EOF'

AutoDL 环境安装完成。
1. 不要把 API Key 写进镜像；每次开机后通过 export 注入。
2. 在实例列表关机，然后选择“更多操作 -> 保存镜像”。
3. 保存后到“镜像”页复制镜像 ID；共享时填写对方用户 ID。
EOF

