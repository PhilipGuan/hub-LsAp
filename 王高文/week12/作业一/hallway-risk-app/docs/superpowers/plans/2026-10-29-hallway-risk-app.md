# 楼道消防风险识别应用实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 `superpowers:executing-plans` 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 创建一个手机优先的网页应用，支持楼道照片拍摄或本地选择，并通过 MiMo 输出消防风险等级和判断依据。

**架构：** FastAPI 提供同源网页和图片分析接口。浏览器负责拍照、预览和上传；后端校验图片、转成 Base64 data URL 并调用 MiMo，照片仅在请求期间保留在内存中。风险结果使用固定字段和枚举，非楼道单独作为场景结果返回。

**技术栈：** Python 3.10+、FastAPI、Uvicorn、python-multipart、原生 HTML/CSS/JavaScript、MiMo OpenAI-compatible Chat Completions API。

---

## 文件职责

- `backend/main.py`：FastAPI 静态页面、图片上传端点、输入校验和错误映射。
- `backend/mimo.py`：消防分类提示词、MiMo HTTP 请求、响应解析和枚举校验。
- `backend/schemas.py`：风险场景、等级及结果响应的 Pydantic 模型。
- `frontend/index.html`：移动端拍摄、上传、预览、识别及结果语义结构。
- `frontend/styles.css`：移动优先布局、取景预览和风险等级样式。
- `frontend/app.js`：摄像头访问与回退、图片选择、上传、进度和结果状态。
- `requirements.txt`：运行依赖。
- `.env.example`：MiMo 模型、API 地址、密钥和上传限制配置示例。
- `.gitignore`：忽略虚拟环境、本地密钥及缓存。
- `README.md`：安装、配置、启动和手机访问说明。

## 任务 1：定义结果格式与 MiMo 适配器

- [x] 在 `backend/schemas.py` 定义 `scene`（`hallway`/`non_hallway`）、`risk_level`（`none`/`low`/`medium`/`high`，非楼道时为空）、`evidence` 和 `recommendation`。
- [x] 在 `backend/mimo.py` 按用户给出的五种场景编写中文提示词；要求模型只返回 JSON，并明确电动车/电瓶/飞线充电优先判高风险、严重通行堵塞或大量可燃物判中等风险、少量堆物判低风险、干净楼道判无隐患。
- [x] 从环境变量读取 `MIMO_API_KEY`、`MIMO_BASE_URL` 和 `MIMO_MODEL`；默认使用 `https://api.xiaomimimo.com/v1` 与 `mimo-v2.6-pro`。
- [x] 将图片作为 `data:{mime};base64,...` 传入；处理超时、HTTP 错误、非 JSON 输出和未知枚举，不把 API 密钥或原始图片写入日志。

## 任务 2：提供 FastAPI 分析接口与静态页面

- [x] 在 `backend/main.py` 挂载 `frontend/`，提供 `GET /` 和 `POST /api/analyze`。
- [x] 接受单张 JPEG、PNG、GIF、WebP 或 BMP 图片；检查图片格式与大小上限，校验失败时返回清楚的 4xx JSON 错误。
- [x] 缺少密钥时返回可操作的配置错误；成功时返回 `RiskAssessment`，MiMo 故障时返回不包含密钥的 502/504 错误。

## 任务 3：实现手机拍摄与上传流程

- [x] 在 `frontend/index.html` 提供“拍照”和“选择本地图片”两个入口、图片预览、重新选择和“开始识别”操作。
- [x] 在 `frontend/app.js` 优先用 `getUserMedia({ video: { facingMode: "environment" } })` 打开后置摄像头并从视频帧生成图片；摄像头不可用或非安全上下文时，回退到带 `capture="environment"` 的文件输入。
- [x] 使用独立文件输入提供相册/文件上传；调用分析接口时显示进度，成功后渲染风险等级、场景依据及建议，失败时保留图片并展示重试提示。
- [x] 在取消、拍摄完成或页面离开时停止摄像头轨道；避免将图片数据写入 localStorage 或后端磁盘。
- [x] 在 `frontend/styles.css` 完成窄屏布局，并以文字标签配合颜色区分风险等级。

## 任务 4：依赖、配置和运行说明

- [x] 编写 `requirements.txt`、`.env.example`、`.gitignore` 和 `README.md`。
- [x] 文档说明本机启动命令、MiMo API Key 配置、单文件上传限制，以及手机通过 HTTPS 访问时的摄像头要求和非安全环境下的原生拍照回退。

## 验收条件

- 本地启动后可打开移动端页面，选择相册照片并显示预览。
- 浏览器支持安全上下文摄像头时可实时预览并拍照；否则可通过手机原生拍照文件输入完成拍摄。
- 上传后能返回五类之一，非楼道结果不显示风险等级；结果包含简短依据和建议。
- 错误密钥、无密钥、无效图片、超限图片及模型服务错误均有可读提示。
- 项目不保存照片或 API Key。
