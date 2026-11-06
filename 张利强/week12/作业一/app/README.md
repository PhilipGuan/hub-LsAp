# 楼道消防安全隐患识别

参考 `../ref/案例-多模态内容理解*.ipynb` 的「Qwen-VL 图文问答」思路，把 Notebook 里的零样本 Prompt 做成一个可用的 Web 应用：

- 手机浏览器直接调用摄像头拍照，或从相册/电脑上传图片
- 后端把图片压缩后以 OpenAI 兼容格式发给多模态大模型
- 模型返回严格 JSON：风险等级、置信度、判定依据、整改建议
- 五档判定：**高风险 / 中风险 / 低风险 / 无风险 / 非楼道**
- 无状态：图片只在内存中处理，不落盘、不入库

## 判定标准

| 等级 | 判定条件 | 展示颜色 |
| --- | --- | --- |
| 高风险 | 楼道出现电动车、电瓶/蓄电池、飞线充电、私拉电线等可能直接起火的元素 | 红 |
| 中风险 | 大量堆积物严重影响通行；或堆放纸箱、木质家具、泡沫、油漆等可燃物，起火后易蔓延 | 橙 |
| 低风险 | 存在堆物，但数量少、体积小，基本不影响通行，无可燃物集中堆放 | 黄 |
| 无风险 | 楼道干净整洁，无堆放物品，通道畅通 | 绿 |
| 非楼道 | 画面主体不是住宅楼道/楼梯间/公共走廊（室内、街道、自拍、截图等） | 灰 |

Prompt 位于 `analyzer.py` 的 `USER_PROMPT`，按「非楼道 → 高风险 → 中风险 → 低风险 → 无风险」的优先级顺序匹配，并强制模型只输出 JSON。

## 目录结构

```
app/
├── main.py              FastAPI 入口：/、/api/health、/api/analyze
├── analyzer.py          图片预处理、模型调用、JSON 解析与校验、演示模式
├── config.py            .env 配置读取
├── static/
│   ├── index.html       单页界面（摄像头 / 上传 / 结果卡片 / 会话记录）
│   ├── style.css        响应式样式，移动端优先
│   └── app.js           摄像头、拖拽上传、粘贴、请求与渲染
├── tests/               unittest 测试（不依赖网络）
├── requirements.txt
├── .env.example
├── run.sh               生成自签证书并以 HTTPS 启动
└── README.md
```

## 快速开始

```bash
cd 张利强/week12/作业二/app

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# 编辑 .env，填入 DASHSCOPE_API_KEY

./run.sh
```

启动后终端会打印两个地址：

```
本机访问 : https://localhost:8443
手机访问 : https://192.168.x.x:8443
```

本机直接打开 `https://localhost:8443`；手机连同一个 Wi-Fi 后打开局域网地址。

> 没有 Key 也可以先看效果：在 `.env` 里设置 `MOCK_MODE=1`，页面右上角会显示「演示模式」，返回的是按图片哈希生成的示例结果，用于验证前端流程。

### 环境变量

> 本项目以 `app/.env` 为准，`.env` 的优先级高于系统环境变量，避免 shell 里遗留的旧 Key / 旧 `MOCK_MODE` 覆盖配置。

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DASHSCOPE_API_KEY` | 空 | 阿里云百炼 API Key，必填（演示模式除外） |
| `VISION_BASE_URL` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | OpenAI 兼容接口地址 |
| `VISION_MODEL` | `deepseek-v4.1-flash` | 视觉模型名称。必须支持图片输入；`deepseek-v4-flash-0731` 是纯文本模型，会忽略图片 |
| `MOCK_MODE` | `0` | 置 1 使用演示数据 |
| `MAX_OUTPUT_TOKENS` | `3000` | 输出上限。推理型模型会先消耗 token 思考，太小会导致 `content` 为空 |
| `MAX_IMAGE_MB` | `10` | 单张图片大小上限 |
| `REQUEST_TIMEOUT` | `90` | 调用模型超时（秒） |
| `IMAGE_MAX_SIDE` | `1280` | 压缩后长边像素 |
| `JPEG_QUALITY` | `85` | 压缩质量 |

## 手机访问与证书

浏览器只在 **HTTPS** 或 **localhost** 下允许 `getUserMedia`，所以 `run.sh` 默认生成证书并以 HTTPS 启动：

1. 首次运行会在 `certs/` 下生成自签证书（安装了 `mkcert` 则优先使用；建议先执行一次 `mkcert -install`，可免去手动信任）。
2. **iPhone**：Safari 打开地址 → 「显示详细信息」→「访问此网站」→ 输入锁屏密码。若仍提示不安全，到「设置 → 通用 → 关于本机 → 证书信任设置」中开启对该证书的完全信任。
3. **Android Chrome**：点「高级」→「继续前往」即可；部分机型需在系统设置中安装证书。
4. 手机与电脑必须在同一局域网，且电脑防火墙放行该端口。

只想在本机调试、不想折腾证书：

```bash
NO_HTTPS=1 ./run.sh      # http://localhost:8443，仅本机可用摄像头
```

## API

### `GET /api/health`

```json
{
  "status": "ok",
  "model": "deepseek-v4.1-flash",
  "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
  "mock": false,
  "key_configured": true,
  "max_image_mb": 10
}
```

### `POST /api/analyze`

`multipart/form-data`，字段名 `file`。

```bash
curl -k -F "file=@stairwell.jpg" https://localhost:8443/api/analyze
```

成功响应：

```json
{
  "risk_level": "中风险",
  "confidence": 0.88,
  "is_stairwell": true,
  "evidence": [
    "楼梯转角堆放多个纸箱和木质柜子，占用约一半通道宽度",
    "堆积物以可燃材料为主，起火后易阻断疏散"
  ],
  "suggestion": "当日清空纸箱、木质家具等可燃堆积物，恢复通道净宽。",
  "model": "deepseek-v4.1-flash",
  "elapsed_ms": 2431,
  "mock": false
}
```

错误统一为 `{"detail": "可直接展示给用户的中文说明"}`，状态码：

| 状态码 | 含义 |
| --- | --- |
| 400 | 图片无法解析 / 为空 |
| 413 | 图片超过大小限制 |
| 415 | 不是图片类型 |
| 429 | 模型服务限流 |
| 502 | 模型服务报错 / 鉴权失败 |
| 503 | 未配置 API Key |
| 504 | 模型响应超时 |

## 实现要点

1. **图片预处理**：`Pillow` 读取并纠正 EXIF 方向，透明图铺白底，长边压到 1280 后转 JPEG，既省 token 又避免手机原图过大。可选安装 `pillow-heif` 支持 iPhone 的 HEIC。
2. **强约束输出**：`response_format={"type":"json_object"}` + Prompt 中给出固定 schema；解析时去掉 ```json 围栏，兼容模型多说一句的情况。
3. **二次校验**：风险等级做别名归一（「中等风险」→「中风险」），置信度兼容 0-100 与百分号，`is_stairwell` 与等级冲突时以等级为准。校验失败会自动带着错误原因重试一次，仍失败则明确报错，不猜测结果。
4. **无状态**：不写数据库、不保存上传文件；「本次会话记录」只存在浏览器内存里，刷新即清空。
5. **失败可读**：401/429/超时/图片损坏分别给出中文提示，前端展示为错误卡片并可重试。

## 测试

```bash
.venv/bin/python -m unittest discover -s tests -v
```

覆盖图片压缩与格式转换、非法图片拒绝、等级别名归一、置信度归一、冲突处理、演示模式确定性，以及接口的成功/415/400 分支。

## 常见问题

**右上角一直显示「演示模式」**

改完 `.env` 必须重启服务。本项目让 `.env` 优先于系统环境变量，但仍要确认 `.env` 里是 `MOCK_MODE=0`，且没有用 `MOCK_MODE=1 ./run.sh` 启动。

**识别失败：模型没有返回结果内容 / 输出被截断**

`deepseek-v4.1-flash` 是推理型模型，会先消耗 token 思考。把 `.env` 中的 `MAX_OUTPUT_TOKENS` 调大（如 `4000`）后重启即可。代码已经会针对「思考占满长度」给出明确提示，而不是静默失败。

**模型说「没有上传图片」，或不管什么图都判「非楼道」**

当前 `VISION_MODEL` 不支持图片输入。`deepseek-v4-flash-0731` 是纯文本模型，会直接忽略图片；请改用 `deepseek-v4.1-flash`、`qwen3-vl-plus` 等视觉模型。

**手机打开白屏 / 摄像头打不开**

确认地址是 `https://` 并已信任自签证书；HTTP 下只有 `localhost` 允许调用摄像头。手机与电脑需在同一 Wi-Fi，且电脑防火墙放行端口。

## 已知限制

- 识别结果依赖模型能力，光线差、遮挡严重或角度怪异时置信度会下降；Prompt 要求「信息不足时选更保守等级并说明原因」。
- 单张图片独立判定，不结合多张照片或楼层上下文。
- 不返回风险区域框选（bbox），需要的话可扩展 schema 并在前端叠加画框。
- 自签证书在手机上需要手动信任；正式部署建议用受信任证书或反向代理。
