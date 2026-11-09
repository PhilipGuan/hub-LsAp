# 楼道消防安全隐患识别（多模态前后端应用）

参考 `02-多模态大模型/案例-多模态内容理解.ipynb`，构建一个带前后端的多模态应用：

- **前端网页**调用手机摄像头拍照，或上传图片
- **后端**调用 DeepSeek Flash V4.1 / Qwen-VL 多模态大模型，实时判断照片内场景是否存在消防安全隐患及危险程度

## 风险分类

| 等级 | 含义 |
| --- | --- |
| 🔴 高风险 | 楼道中出现电动车、电瓶、飞线充电等可能起火的元素 |
| 🟠 中风险 | 大量堆积物严重影响通行，或堆放大量纸箱、木质家具等易蔓延的堵塞物 |
| 🟡 低风险 | 存在楼道堆物现象但不严重 |
| 🟢 无风险 | 楼道干净，无堆放物品 |
| ⚪ 非楼道 | 与楼道无关的图片 |

## 目录结构

```
楼道风险识别/
├── backend/
│   ├── app.py            # FastAPI 入口（路由 + 图片接收 + 服务前端页面）
│   ├── providers.py      # DeepSeek / Qwen 多模态调用 + 风险解析
│   ├── config.py         # API Key / 模型 / 端点配置
│   ├── requirements.txt  # 依赖
│   ├── .env.example      # 配置示例
│   └── .env              # 实际配置（填入你的 Key）
└── frontend/
    └── index.html        # 前端页面（拍照 + 上传 + 结果展示）
```

## 快速开始

### 1. 安装依赖

```bash
cd 楼道风险识别/backend
pip install -r requirements.txt
```

### 2. 配置 API Key

编辑 `backend/.env`，至少填一个供应商：

```env
# DeepSeek Flash V4.1（https://platform.deepseek.com 申请）
DEEPSEEK_API_KEY=sk-你的key

# 通义千问 Qwen-VL（https://bailian.console.aliyun.com 申请）
DASHSCOPE_API_KEY=sk-你的key
```

> 两个供应商均为 **OpenAI 兼容接口**，后端用同一套代码调用，前端可切换模型。

### 3. 启动后端

```bash
cd 楼道风险识别/backend
uvicorn app:app --host 0.0.0.0 --port 8000
```

### 4. 打开网页

- 本机访问：<http://localhost:8000>
- 手机访问（同一局域网）：`http://<电脑局域网IP>:8000`，用 `ifconfig` / `ipconfig` 查本机 IP

## 手机摄像头注意事项

浏览器调用摄像头（`getUserMedia`）要求**安全上下文**：

- ✅ `http://localhost` 属于安全上下文，电脑浏览器可直接用摄像头
- ⚠️ 手机通过局域网 `http://192.168.x.x:8000` 访问时，**不是**安全上下文，摄像头会被浏览器拦截
- ✅ 解决办法（任选其一）：
  1. 直接用「**上传图片**」功能（手机相册或相册拍照再上传，不受限制）
  2. 用内网穿透工具拿到 HTTPS 地址，例如 `cloudflared tunnel --url http://localhost:8000` 或 `ngrok http 8000`

## 接口说明

### `POST /api/analyze`

`multipart/form-data` 表单：

| 字段 | 说明 |
| --- | --- |
| `file` | 图片文件（jpeg/png/webp/gif，≤32MB） |
| `provider` | `qwen`（默认）或 `deepseek` |

返回示例：

```json
{
  "risk": "高风险",
  "level": "high",
  "severity": 4,
  "color": "#dc2626",
  "reason": "楼道内停放电动车并存在飞线充电",
  "provider": "qwen",
  "provider_name": "Qwen-VL",
  "model": "qwen-vl-max-latest",
  "raw": "{\"risk\": \"高风险\", \"reason\": \"...\"}"
}
```

### `GET /api/providers`

返回各供应商是否已配置 Key，前端据此禁用未配置的选项。

## 技术要点

- **图片入模型**：后端把图片 base64 编码成 `data:image/jpeg;base64,...`，放在 user 消息的 `image_url` 中
- **结构化输出**：提示词要求模型只返回 JSON `{"risk": "...", "reason": "..."}`，后端 `parse_risk` 解析并做关键词兜底，保证结果稳定
- **分类标准**：与案例 notebook 完全一致（高风险/中风险/低风险/无风险/非楼道）
