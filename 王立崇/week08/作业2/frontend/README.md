# 深度研究助手 · 前端

综合案例 02 的前端（Next.js App Router + TypeScript + Tailwind CSS），负责输入研究主题、实时展示研究过程、呈现四类产物。

## 运行

```bash
# 1. 安装依赖（首次）
npm install

# 2. 启动后端（在项目根目录，另开一个终端）
bash start.sh          # 或 uvicorn backend.app:app --port 8000

# 3. 启动前端（本目录）
npm run dev            # 默认 http://localhost:3000
```

后端地址默认 `http://127.0.0.1:8000`，如后端改端口（如 8001），编辑 `.env.local`：

```
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8001
```

## 页面

- `/` 首页：输入研究主题（`POST /api/research`）+ 历史研究列表（`GET /api/research`）+ 后端连接状态。
- `/research/[id]` 详情页：每 2 秒轮询 `GET /api/research/{id}`，按状态渲染：
  - `pending/running`：实时进度（轮次、检索词、正文段落数、来源数、逐步日志）；
  - `completed`：结构化报告、完整 HTML 报告、来源列表、研究过程记录、置信度说明；
  - `failed`：错误信息。

## 目录

```
app/
├── layout.tsx                全局布局
├── page.tsx                  首页
├── research/[id]/page.tsx    研究详情页
└── globals.css               Tailwind 入口
components/                   展示组件（报告/来源/过程/置信度/进度/表单/列表）
lib/
├── api.ts                    类型（对齐 backend/models.py）+ API 客户端
├── format.ts                 过程记录展示格式化
└── useResearch.ts            轮询 hook
```
