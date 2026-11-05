# SQL Agent 前端（Vue 3 + Vite）

对话式查询 Chinook 数据库的网页界面，后端是同一项目的 FastAPI 服务
（`sql_agent_langgraph/api.py`）。

## 启动

先启动后端（在 `week13` 目录）：

```powershell
conda activate py312
python -m sql_agent_langgraph.api
```

再启动前端：

```powershell
cd F:\newStudy\homework\hub-LsAp\陈晓清\week13\frontend
npm install
npm run dev
```

打开 http://localhost:5173

开发服务器会把 `/api` 代理到 `http://127.0.0.1:8000`，所以不需要处理跨域。
如果后端在别的地址，复制 `.env.example` 为 `.env.local` 并改
`VITE_API_TARGET`。

## 构建

```powershell
npm run build
npm run preview
```

生产环境可以把 `dist/` 交给 Nginx 等静态服务器，并把 `/api` 反向代理到
FastAPI；或者设置 `VITE_API_BASE_URL` 指向后端完整地址后重新构建。

也可以让 FastAPI 直接托管构建产物（项目已支持）：

```powershell
cd F:\newStudy\homework\hub-LsAp\陈晓清\week13\frontend
npm run build

cd ..
python -m sql_agent_langgraph.api
```

然后直接访问 http://127.0.0.1:8000 ，前端和 API 在同一个端口。

## 功能

- 发送问题，展示自然语言答案、生成的 SQL、涉及的表、返回行数
- 展示数据依据（evidence）、注意事项（caveats）、自动修复次数和耗时
- 服务端会话历史：刷新页面后通过 `session_id` 恢复对话
- 新建对话：删除服务端会话并清空本地记录
- 顶栏显示后端连接状态和当前模型，点击可重试连接
- 响应式布局，支持手机端

会话 ID 保存在 `localStorage`，每个会话的历史保存在 FastAPI 进程内存中。
