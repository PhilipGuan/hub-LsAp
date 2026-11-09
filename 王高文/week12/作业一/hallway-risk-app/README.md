# 楼道消防风险识别

手机优先的本地 Web 应用：拍摄或选择一张照片，后端调用 Xiaomi MiMo 图片理解，返回楼道场景、风险等级、可见依据和处置建议。应用代码不将照片保存到项目目录；照片会发送给 MiMo 完成本次分析。

## 环境要求

- Python 3.10 或更高版本
- Xiaomi MiMo API Key

## 安装与启动（Windows PowerShell）

```powershell
cd hallway-risk-app
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

在 `.env` 中设置 `MIMO_API_KEY`。默认 API 地址为 `https://api.xiaomimimo.com/v1`，模型为 `mimo-v2.6-pro`；也可调整 `MIMO_BASE_URL` 和 `MIMO_MODEL`。不要提交 `.env` 或把密钥放进前端代码。

电脑打开 <http://127.0.0.1:8000>。手机与电脑连接同一网络后，可用电脑的局域网 IP 访问，例如 `http://192.168.1.20:8000`。浏览器实时摄像头预览要求 HTTPS 或 localhost 安全上下文；通过局域网 HTTP 打开时，“拍照”会回退到手机原生拍摄入口。若浏览器未提供该入口，可使用“选择本地图片”。

## 识别结果

- **高风险**：电动车、电瓶、飞线充电等起火风险元素。
- **中等风险**：严重堵塞通行，或大量纸箱、木质家具等可燃物。
- **低风险**：少量堆物且未明显影响通行。
- **无隐患**：楼道整洁，没有明显堆物。
- **非楼道**：照片不属于楼道场景。

图片格式支持 JPEG、PNG、GIF、WebP、BMP；单张默认上限 15 MB。MiMo API 图片理解接口支持 Base64 图片输入，应用通过服务端请求该接口。浏览器 `getUserMedia()` 需安全上下文；详见 [MiMo 图片理解文档](https://mimo.mi.com/docs/zh-CN/quick-start/usage-guide/multimodal-understanding/image-understanding) 和 [MDN 摄像头权限说明](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia)。

模型结果用于辅助巡查，现场处置仍应结合实际情况核实。
