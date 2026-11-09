# 楼道消防隐患识别 App（多模态）

带前/后端的多模态应用：前端网页调用手机摄像头拍照或上传图片，后端调用
**DeepSeek 多模态模型 `deepseek-flash`（DeepSeek-V4.1-Flash-1017）** 实时识别
楼道内是否存在消防安全隐患，并给出危险程度分级。

## 风险分级

| 等级 | 判定标准 |
|------|----------|
| 🔴 高风险 | 楼道中出现电动车、电瓶、飞线充电等可能起火的元素 |
| 🟠 中风险 | 楼道中存在大量堆积物严重影响通行，或堆放大量纸箱、木质家具等能造成火势蔓延的堵塞物 |
| 🟡 低风险 | 存在楼道堆物现象但不严重 |
| 🟢 无风险 | 楼道干净，无堆放物品 |
| ⚪ 非楼道 | 图片内容与楼道无关 |

## 目录结构

```
corridor-risk-app/
├── backend/
│   ├── main.py           # FastAPI 服务 + 托管前端
│   ├── vision.py         # DeepSeek 视觉识别客户端（OpenAI 兼容）
│   ├── prompt.py         # 分类 prompt
│   ├── config.py         # 环境变量配置
│   ├── .env              # 你的 API Key（已 gitignore）
│   └── requirements.txt
└── frontend/
    ├── index.html        # 页面
    ├── app.js            # 相机 / 上传 / 结果渲染
    └── style.css
```

## 快速开始

### 1. 安装依赖

```bash
pip install fastapi uvicorn requests python-dotenv python-multipart
```

### 2. 配置 API Key

编辑 `backend/.env`，填入你的 DeepSeek API Key（到 <https://platform.deepseek.com> 获取）：

```ini
DEEPSEEK_API_KEY=sk-你的key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-flash
```

> 模型 `deepseek-flash` 即 DeepSeek-V4.1-Flash-1017，是目前唯一支持图像输入的官方模型 ID。
> 若将来模型 ID 变更，只需改 `.env` 里的 `DEEPSEEK_MODEL`，无需改代码。

### 3. 启动后端

```bash
cd backend
python main.py
```

服务默认监听 `0.0.0.0:8000`，同时托管前端页面。

### 4. 打开网页

- **电脑本机**：<http://localhost:8000>
- **手机访问**（与电脑同一局域网）：`http://<电脑局域网IP>:8000`

## 手机摄像头说明（重要）

浏览器的 `getUserMedia` 摄像头接口**只在安全上下文（HTTPS 或 localhost）下可用**。

- 电脑本机用 `http://localhost:8000` 访问 → 摄像头可用（需电脑有摄像头）。
- 手机用 `http://<局域网IP>:8000` 访问 → 属非安全上下文，**摄像头会被浏览器拦截**。
  此时请改用页面上的「🖼️ 上传图片」，手机端点击后同样可以选择「拍摄照片」。
- 若要手机也能直接网页内拍照，请给服务套一层 HTTPS（推荐用隧道工具，如
  `cloudflared tunnel` 或 `ngrok`，会生成一个 https 域名指向本机 8000 端口）。

## 接口说明

| 方法 | 路径 | 说明 |
|------|------|------|
| GET  | `/api/health` | 健康检查，返回模型名与 key 是否已配置 |
| POST | `/api/analyze` | 上传图片（multipart/form-data，字段名 `file`），返回识别结果 |

`/api/analyze` 返回示例：

```json
{
  "ok": true,
  "risk_level": "高风险",
  "risk_code": "high",
  "summary": "楼道内停放着一辆电动车",
  "reason": "检测到电动车，存在起火风险，判定为高风险。",
  "raw": "...模型原始输出..."
}
```

`risk_code` 取值：`high` / `medium` / `low` / `none` / `non_corridor` / `unknown`。

## 换用其它视觉模型（可选）

后端接口走 OpenAI 兼容格式，改 `.env` 即可切换到其它多模态模型，例如：

- **阿里百炼 Qwen-VL**：`DEEPSEEK_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1`，`DEEPSEEK_MODEL=qwen-vl-max`
- **智谱 GLM-4V-Flash**：`DEEPSEEK_BASE_URL=https://open.bigmodel.cn/api/paas/v4`，`DEEPSEEK_MODEL=glm-4v-flash`
