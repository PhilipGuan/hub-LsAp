#!/usr/bin/env bash
# ------------------------------------------------------------
# BGE 文本检索实验室 · 一键全流程
# 作用：依次执行 安装依赖 → 下载模型 → 最小 Demo → 进阶 Demo
# 用法：bash ./run_all.sh
# ------------------------------------------------------------
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'
log()   { printf "${BLUE}[INFO]${NC} %s\n" "$*"; }
ok()    { printf "${GREEN}[ OK ]${NC} %s\n" "$*"; }
warn()  { printf "${YELLOW}[WARN]${NC} %s\n" "$*"; }

log "项目根目录: $ROOT_DIR"

# ---------- 0. Python 环境检查 ----------
PY_BIN="${PYTHON_BIN:-python3}"
if [ -f "/Users/philipclaw/Downloads/padow-ai/.venv/bin/python" ]; then
    PY_BIN="/Users/philipclaw/Downloads/padow-ai/.venv/bin/python"
    log "检测到项目虚拟环境: $PY_BIN"
fi

log "检查 Python 版本..."
"$PY_BIN" -c "import sys; assert sys.version_info >= (3,10), f'需要 Python>=3.10, 当前 {sys.version}'"
ok "Python OK: $("$PY_BIN" -V 2>&1)"

# ---------- 1. 安装依赖 ----------
log "Step 1/4 · 检查/安装依赖..."
"$PY_BIN" -m pip install -q -r requirements.txt
ok "依赖就绪"

# ---------- 2. 下载模型 ----------
log "Step 2/4 · 下载 BAAI/bge-small-zh-v1.5 (首次约 190MB)..."
"$PY_BIN" ./01_下载模型.py
ok "模型就绪"

# ---------- 3. 最小 Demo ----------
log "Step 3/4 · 运行最小检索 Demo (查询=我今天很开心)"
"$PY_BIN" ./02_最小检索Demo.py
ok "最小 Demo 通过"

# ---------- 4. 进阶 Demo ----------
log "Step 4/4 · 运行 SimpleBGEVectorStore 进阶版 (3 组查询 × 6 条文档)"
"$PY_BIN" ./03_可复用向量库.py
ok "进阶 Demo 通过"

echo ""
ok "============================================================"
ok "  全部 4 步执行成功！请打开 README.md 了解第一性原理讲解"
ok "============================================================"
