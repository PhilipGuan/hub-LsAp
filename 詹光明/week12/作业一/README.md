# week12 作业一：楼道消防安全隐患识别

对应 `作业一.py` 顶部注释的需求：前端用手机拍照并上传，后端调用多模态模型判断楼道消防
安全隐患等级（高风险 / 中风险 / 低风险 / 无风险 / 非楼道）。目前前后端均已实现：
网页调用摄像头拍照或从相册上传，后端返回等级、隐患元素和判断依据。

## 文件说明

| 文件 | 说明 |
| --- | --- |
| `risk_analyzer.py` | 推理核心：图片预处理、提示词、本地 Qwen2.5-VL / 远程视觉 API、结果解析 |
| `server.py` | FastAPI 服务：上传识别、健康检查、分级说明、图片留档 |
| `web/index.html` | 前端页面：调用手机/电脑摄像头拍照、相册上传、展示识别结果 |
| `requirements.txt` | 依赖清单 |
| `作业一.py` | 参考示例（原样保留） |

## 启动

```bash
# 在仓库根目录安装依赖（torch 需按 CUDA 版本安装，见 PyTorch 官网）
pip install -r 詹光明/week12/requirements.txt

# 启动服务（默认 0.0.0.0:8000，手机可经局域网访问）
python 詹光明/week12/server.py
```

模型默认自动查找仓库根目录的 `model/`（Qwen2.5-VL-7B-Instruct）。服务启动后模型在后台
线程加载，前端先轮询 `/api/health`，`ready=true` 后再上传图片。

命令行单张测试（不启动 Web 服务）：

```bash
python 詹光明/week12/risk_analyzer.py 某张图片.jpg
```

## 前端使用

浏览器打开 `http://localhost:8000/`（电脑本机）即可：

- 页面启动后请求摄像头（优先后置），点圆形快门拍照，照片自动上传并识别；
- 「相册上传」选择已有照片；摄像头不可用时还会出现「系统相机拍摄」按钮（调用手机原生相机）；
- 顶部状态显示后端/模型状态，首次加载模型时页面可正常打开、稍等即可；
- 识别完成后展示风险等级（按等级配色）、隐患标签、判断依据和模型原始输出；
- 点「重新拍摄 / 上传」回到拍照界面。

### 手机通过局域网访问与 HTTPS

浏览器只在 **HTTPS 或 localhost** 下允许网页调用摄像头。电脑本机用 localhost 即可；
手机通过局域网 IP 访问时需要用 HTTPS（自签证书即可），否则页面会提示改用相册/系统相机。

```bash
# 在 week12 目录下，把 IP 换成本机局域网 IP
openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
  -keyout key.pem -out cert.pem \
  -subj "/CN=192.168.1.10" -addext "subjectAltName=IP:192.168.1.10"

# 启动带 TLS 的服务
RISK_SSL_CERT=cert.pem RISK_SSL_KEY=key.pem python server.py
```

手机访问 `https://192.168.1.10:8000`，首次打开需在浏览器中信任该自签证书。
（`cert.pem` / `key.pem` 已被 `.gitignore` 忽略，不要提交。）

## 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `RISK_PROVIDER` | `local` | `local` 本地模型；`remote` 调用 OpenAI 兼容视觉接口 |
| `RISK_MODEL_PATH` | 自动查找 | 本地模型目录 |
| `RISK_DEVICE_MAP` | `auto` | 显存不足时自动卸载到 CPU |
| `RISK_DTYPE` | `auto` | `float16` / `bfloat16` / `float32` |
| `RISK_LOAD_IN_4BIT` | `auto` | `auto`：bf16 权重放不进显存且已安装 bitsandbytes 时启用 4-bit；`1` 强制启用；`0` 关闭 |
| `RISK_GPU_WEIGHTS_FRACTION` | `0.72` | `device_map=auto` 时权重最多占空闲显存的比例，剩余留给推理激活 |
| `RISK_MAX_MEMORY` | — | 直接指定显存预算（如 `9GiB`），优先于上一项 |
| `RISK_MAX_IMAGE_SIDE` | `2048` | 上传图片长边上限，控制视觉 token 数 |
| `RISK_MAX_NEW_TOKENS` | `512` | 生成 token 上限 |
| `RISK_MAX_UPLOAD_MB` | `20` | 上传大小限制 |
| `RISK_SAVE_UPLOADS` | `1` | 是否把上传图片留档到 `data/uploads/` |
| `RISK_PRELOAD` | `1` | 是否启动时后台预加载模型 |
| `RISK_UPLOAD_DIR` | `week12/data/uploads` | 留档目录 |
| `RISK_SSL_CERT` / `RISK_SSL_KEY` | — | 配置后以 HTTPS 启动（手机摄像头需要） |
| `RISK_API_BASE` / `RISK_API_KEY` / `RISK_API_MODEL` | — | remote 模式必填，如 DashScope 的 `https://dashscope.aliyuncs.com/compatible-mode/v1` + `qwen-vl-max` |

## 接口

### POST /api/analyze

`multipart/form-data`，字段名 `file`，可选字段 `save_image=true|false`。

```bash
curl -X POST http://localhost:8000/api/analyze -F "file=@楼道照片.jpg"
```

```json
{
  "risk_level": "高风险",
  "risk_code": "high",
  "risk_score": 3,
  "hazards": ["电动车"],
  "reason": "楼道内停放电动车，存在起火风险",
  "raw_output": "{\"risk_level\": \"高风险\", ...}",
  "inference_ms": 3210,
  "provider": "local",
  "model": "model",
  "request_id": "0f1e...",
  "filename": "楼道照片.jpg",
  "width": 2048,
  "height": 1536,
  "elapsed_ms": 3402,
  "image_path": "data/uploads/20261028-225500-ab12cd34.jpg",
  "created_at": "2026-10-28T22:55:00"
}
```

模型输出无法解析时返回 `risk_level = "无法判断"`、`risk_code = "unknown"`，并保留
`raw_output` 便于排查；模型未就绪返回 503，图片无法解析返回 400，超过大小返回 413。

### GET /api/health

```json
{"status": "ok", "ready": true, "provider": "local", "model": "...", "load_error": null}
```

### GET /api/levels

返回五个等级及说明，可直接用于前端图例。

## 说明与限制

- 实测环境：RTX 4070 SUPER 12GB，7B 模型 bf16 约 16.7GB。默认 `RISK_LOAD_IN_4BIT=auto`
  在检测到权重放不进显存时自动启用 nf4 4-bit 量化（约 5GB），模型完整放入显存：
  **首次加载约 18 秒，单张推理约 6 秒**；关闭量化后部分层卸载到 CPU，单张约 114 秒。
- 若 4-bit 模式加载失败，可检查 `bitsandbytes` 是否与 CUDA 匹配，或设置
  `RISK_LOAD_IN_4BIT=0` 回到 bf16 + CPU 卸载模式（更慢但可用）。
- 首次加载时 `/api/analyze` 会等待模型就绪；前端应先轮询 `/api/health` 的 `ready`。
- 国内网络安装依赖较慢可用镜像：`pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/`
- 摄像头相关限制（HTTPS / localhost）与局域网配置见上文「前端使用」。
