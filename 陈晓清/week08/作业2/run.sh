#!/usr/bin/env bash
# 启动深度研究助手服务。用法：bash run.sh [端口]（默认 8000）
# Windows 下在 Git Bash 里跑即可；想改代码自动重载，给 uvicorn 加 --reload。
set -euo pipefail
cd "$(dirname "$0")"  # 从任何目录调用都能定位到项目根：`app` 包要能从 cwd 导入

PORT="${1:-8000}"

# 选解释器：PowerShell 里的 `bash` 可能落到 WSL，那儿的 `python` 是 2.7——
# 直接拿它跑会炸在语法上，看不出真正原因。所以先验版本，不行就退到 python3。
PYTHON="${PYTHON:-python}"
if ! "${PYTHON}" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' 2>/dev/null; then
  PYTHON=python3
fi
if ! "${PYTHON}" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' 2>/dev/null; then
  echo "没找到 Python 3.10+（\$PYTHON=${PYTHON:-python}）。若在 PowerShell 下，bash 可能是 WSL。" >&2
  echo "改用 Git Bash，或指定解释器：PYTHON=/c/Python312/python.exe bash run.sh" >&2
  exit 1
fi

# 起服务前先过一遍配置自检（复用 app/config.py 的自测）：少一个 key 就别等跑到一半才发现
if ! "${PYTHON}" -m app.config; then
  echo "自检没过（用的是 $("${PYTHON}" -V 2>&1)）。两种常见原因：" >&2
  echo "  1) .env 少了 key → 检查 LLM_BASE_URL / LLM_API_KEY / BOCHA_API_KEY" >&2
  echo "  2) 这个解释器没装依赖 → pip install -r requirements.txt，或 PYTHON=... 指定另一个" >&2
  exit 1
fi

echo "服务启动中：http://127.0.0.1:${PORT}/health （Ctrl-C 停止）"
exec "${PYTHON}" -m uvicorn app.main:app --host 127.0.0.1 --port "${PORT}"
