# FoodLens 本地启动 / 关闭 / 重启手册

本手册覆盖在本地**完整启动**、**关闭**与**重启** FoodLens 的全部命令（macOS / zsh）。

## 0. 进程与端口总览

FoodLens 本地运行时是一组互相依赖的进程：

| 进程 | 端口 | 作用 | 启动目录 |
| --- | --- | --- | --- |
| 后端 FastAPI（Uvicorn） | `8200` | 提供 `/api/analyze`、`/api/recompute`、`/api/health` | `backend/` |
| 前端 Vite dev server | `5180` | 页面 + 把 `/api` 反向代理到 `8200` | `frontend/` |
| cloudflared 隧道（可选） | — | 把 `5180` 暴露成公网 `*.trycloudflare.com` | 任意 |

> 前端 dev server 自带代理（`vite.config.ts` 中 `/api` → `http://127.0.0.1:8200`），因此**只需访问 `http://localhost:5180`** 即可，无需单独打开后端地址。

依赖关系：先起后端，再起前端。**关停顺序相反**：先停前端，再停后端。

## 1. 前置要求

```bash
# 检查工具链是否就绪
uv --version       # 后端依赖与运行（Python ≥ 3.11）
node --version     # 前端（Node 20+ 建议）
npm --version
cloudflared --version   # 仅当需要公网隧道时
```

项目根目录（下文以 `$PROJ` 指代）：

```bash
export PROJ=/Users/philipclaw/Downloads/padow-ai/Week12/FoodLens
```

## 2. 首次安装（仅需一次）

```bash
# 后端依赖
cd "$PROJ/backend"
uv sync

# 后端配置（真实 VLM 才需要填 Key；demo 模式可跳过）
cp -n .env.example .env

# 前端依赖
cd "$PROJ/frontend"
npm install
```

## 3. 启动

### 3.1 启动后端

**Demo 模式（零 Key，推荐先跑通）：**

```bash
cd "$PROJ/backend"
VISION_PROVIDER=demo uv run uvicorn app.main:app --host 127.0.0.1 --port 8200
```

**真实 VLM 模式（读取 `.env`）：**

```bash
cd "$PROJ/backend"
uv run uvicorn app.main:app --host 127.0.0.1 --port 8200
```

> `--reload` 可在开发时热重载（改代码自动重启）；生产/演示不要加。
> 若手机要从**局域网**直连后端，把 `--host 127.0.0.1` 改为 `--host 0.0.0.0`。

启动成功标志：

```text
INFO:     Uvicorn running on http://127.0.0.1:8200 (Press CTRL+C to quit)
```

验证：

```bash
curl -s http://127.0.0.1:8200/api/health
# {"provider":"demo","status":"ok","nutrition_count":224}
```

### 3.2 启动前端

```bash
cd "$PROJ/frontend"
npm run dev
```

成功标志：

```text
  ➜  Local:   http://localhost:5180/
```

浏览器打开 <http://localhost:5180> 即可使用。

### 3.3 （可选）启动公网隧道

```bash
cloudflared tunnel --url http://localhost:5180
```

输出中的 `https://<随机>.trycloudflare.com` 即为公网地址。前端 `allowedHosts` 与后端 CORS 均已放行该域名，无需额外配置。

## 4. 关闭

### 4.1 前台运行（推荐方式）

在各自终端窗口按 `Ctrl+C`：

```text
^C
INFO:     Shutting down
INFO:     Application shutdown complete.
```

顺序：先 `Ctrl+C` 前端（Vite），再 `Ctrl+C` 后端（Uvicorn），最后 `Ctrl+C` 隧道。

### 4.2 按端口强制关闭（终端丢了 / 后台跑飞了）

```bash
# 关闭后端（8200）
lsof -ti tcp:8200 | xargs kill

# 关闭前端（5180）
lsof -ti tcp:5180 | xargs kill

# 关闭 cloudflared 隧道
pkill -f "cloudflared tunnel" || true

# 若进程顽固不退，把 kill 换成 kill -9
# lsof -ti tcp:8200 | xargs kill -9
```

校验端口是否已释放（**无输出**即已关闭）：

```bash
lsof -i tcp:8200 -i tcp:5180
```

### 4.3 一键全部关闭

```bash
kill_foodlens() {
  lsof -ti tcp:8200 | xargs kill 2>/dev/null || true
  lsof -ti tcp:5180 | xargs kill 2>/dev/null || true
  pkill -f "cloudflared tunnel" 2>/dev/null || true
  sleep 1
  echo "已关闭；以下端口应无输出："
  lsof -i tcp:8200 -i tcp:5180 || echo "  (全部已释放)"
}
```

## 5. 重启

### 5.1 手动重启

```bash
# 1) 先关（见第 4 节），确认端口已释放
lsof -i tcp:8200 -i tcp:5180

# 2) 再按第 3 节重新启动（后端 → 前端 → 可选隧道）
```

### 5.2 一键重启脚本（复制到终端即可用）

```bash
restart_foodlens() {
  local PROJ=/Users/philipclaw/Downloads/padow-ai/Week12/FoodLens

  # ---- 关闭旧进程 ----
  lsof -ti tcp:8200 | xargs kill 2>/dev/null || true
  lsof -ti tcp:5180 | xargs kill 2>/dev/null || true
  sleep 1

  # ---- 启动后端（demo 模式；换成 `uv run uvicorn ...` 即用真实 VLM）----
  ( cd "$PROJ/backend" && VISION_PROVIDER=demo \
      uv run uvicorn app.main:app --host 127.0.0.1 --port 8200 \
      > /tmp/foodlens-backend.log 2>&1 & )

  # ---- 启动前端 ----
  ( cd "$PROJ/frontend" && npm run dev > /tmp/foodlens-frontend.log 2>&1 & )

  sleep 4
  echo "后端健康检查：$(curl -s http://127.0.0.1:8200/api/health)"
  echo "前端： http://localhost:5180"
  echo "日志： /tmp/foodlens-backend.log  /tmp/foodlens-frontend.log"
}
```

运行 `restart_foodlens` 即可完成「关闭 + 重启 + 健康检查」。

> 需查看实时日志：`tail -f /tmp/foodlens-backend.log`（或前端日志同名文件）。

## 6. 数据与配置变更后的重启

代码改动会被 Uvicorn/Vite 热加载或需手动重启；**数据与配置改动必须重启后端**：

| 改动 | 是否需要重启后端 | 说明 |
| --- | --- | --- |
| 改 `app/nutrition/data/*.json` | **是** | 营养库有 `lru_cache`，进程内只加载一次 |
| 改 `backend/.env` | **是** | `Settings` 启动时读取 |
| 改后端任意 `.py` | 是（或 `--reload` 自动） | Uvicorn 启动时才导入 |
| 改前端 `src/**` | 否 | Vite HMR 自动生效 |

**重新摄入 USDA 数据**（不需要跑服务，独立脚本）：

```bash
cd "$PROJ/backend"
# Key 读取顺序：--api-key > 环境变量 USDA_API_KEY > .env > DEMO_KEY
uv run python scripts/ingest_usda.py            # 重新生成 natural_usda.json + _meta.json
uv run python scripts/ingest_usda.py --check    # 只比对差异，不写盘
```

摄入完成后，按第 5 节重启后端使新数据生效。

## 7. 测试（不启动服务）

```bash
cd "$PROJ/backend"
uv run pytest -q          # 单元测试 + 评估集回归（30 passed）
```

前端构建与静态检查：

```bash
cd "$PROJ/frontend"
npm run lint
npm run build
```

## 8. 常见问题排查

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `Address already in use` | 端口被占用（上次没关干净） | `lsof -ti tcp:8200 \| xargs kill`（同理 5180），再启动 |
| 页面能开但「分析」报错 | 后端没起或端口不对 | `curl http://127.0.0.1:8200/api/health` 确认后端存活 |
| `未配置 LLM_API_KEY / LLM_BASE_URL` | 用了真实模式但 `.env` 没填 | 填好 `backend/.env` 后**重启后端**，或改用 `VISION_PROVIDER=demo` |
| VLM 调用失败 `4xx/5xx` | Key / Base URL / 模型名有误，或额度问题 | 核对 `.env` 三个变量；查看后端日志 |
| 隧道打开报 403 | Vite 未放行隧道域名 | 确认 `vite.config.ts` 含 `allowedHosts: ['.trycloudflare.com']` 并**重启前端** |
| 手机打不开局域网地址 | 只监听了本机 | 后端加 `--host 0.0.0.0`；前端 `npm run dev -- --host` |
| 新加的食物查不到 | 营养库缓存未刷新 | **重启后端**；确认新条目文件不以 `_` 开头（`_` 前缀会被跳过） |
