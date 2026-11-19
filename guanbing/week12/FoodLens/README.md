# FoodLens 食物热量识别

> **拍一餐，看懂这一餐。**
> Photograph a meal. Estimate its calories and macros.

**v1.0** · Web 应用：拍摄一餐（多道菜同框）或包装食品营养标签，**逐道**估算热量与三大营养素，并支持轻量修正。

技术栈：React 19 + TypeScript + Vite 8 / FastAPI + Pydantic v2 / 多模态 VLM + USDA 开放营养数据。

---

## 目录

- [这个项目想解决什么](#这个项目想解决什么)
- [核心能力](#核心能力)
- [技术选型与外部 API](#技术选型与外部-api)
- [产品架构](#产品架构)
- [如何用开放数据提升热量计算精度](#如何用开放数据提升热量计算精度)
- [目录结构](#目录结构)
- [快速开始](#快速开始)
- [环境变量](#环境变量)
- [API 参考](#api-参考)
- [营养库与数据维护](#营养库与数据维护)
- [测试与评估](#测试与评估)
- [部署](#部署)
- [已知局限](#已知局限)
- [数据来源与许可](#数据来源与许可)

---

## 这个项目想解决什么

上一站 [UrbanUnbox](../UrbanUnbox) 得出一个关键结论：**VLM 擅长感知与判别，但不擅长开放集实体识别；事实与数值应当走检索，而不是让模型「背」下来。**

「拍张照片算热量」的常见做法是让多模态模型直接看图报数字——这类结果**不可复现、不可追溯、无法修正**，同一张图问两次可能得到两个答案。FoodLens 从第一天就按上述结论设计，遵守两条铁律：

1. **VLM 只做感知** —— 菜品识别、营养标签 OCR、粗分量估计。**所有热量与营养数值一律来自营养数据库 + 确定性计算**，不由模型生成。
2. **每条结果可解释、可修正** —— 标明命中的库条目（`matched_id`）与来源（`source`）；用户改克数 / 增删菜品后由后端重算，**不重新调用 VLM**。

一句话：**模型负责「这是什么」，数据库负责「有多少热量」，两者都不越界。**

## 核心能力

| 能力 | 说明 |
| --- | --- |
| **多菜品识别** | 一餐多菜同框，VLM 逐道返回中英菜名 + 克数估计 + 置信度 |
| **营养库检索** | 224 条（中餐菜肴 + USDA 天然食物），菜名（中/英/别名）精确 → 别名 → 模糊匹配，命中即标注库条目 id 与来源 |
| **确定性计算** | 每道菜 `每100g 营养 × 克数/100`，汇总总量与三大营养素 |
| **分量护栏** | VLM 克数明显偏离库中标准份时向合理区间收敛并标注「已校准」 |
| **轻量修正** | 克数滑杆 / 增删菜品，防抖 400ms 后调重算接口，**不重调 VLM** |
| **营养标签 OCR** | 读取包装食品营养成分表，自动 kJ→kcal 换算、按每份折算为每 100g |
| **中英双语** | 接口同时返回双语字段，前端本地切换，无需重新请求 |
| **未收录诚实** | 库中无此菜时标「未收录」，**不编造热量**，且不计入总量 |

## 技术选型与外部 API

### 前端

| 项 | 选择 |
| --- | --- |
| 框架 | React 19 + TypeScript |
| 构建 | Vite 8（dev server `:5180`，`/api` 反向代理到后端 `:8200`） |
| 代码检查 | oxlint |

### 后端

| 项 | 选择 |
| --- | --- |
| 框架 | FastAPI + Uvicorn |
| 数据模型 | Pydantic v2（`schemas.py` 即 API 契约） |
| 配置 | pydantic-settings，从 `.env` 读取 |
| 依赖与运行 | uv |
| HTTP 客户端 | httpx（异步） |

### 物体识别 API（感知层）

**调用的是 Qwen3-VL-Plus 多模态大模型，通过 OpenAI 兼容的 Chat Completions 接口访问。**

- 模型：`qwen3-vl-plus`（`LLM_MODEL`）
- 协议：OpenAI 兼容 `POST {LLM_BASE_URL}/chat/completions`（`LLM_BASE_URL` 形如 `https://<endpoint>/compatible-mode/v1`，本项目复用阿里云百炼 / Model Studio 托管的兼容端点）
- 传图方式：图片读为字节后 **base64 内联**到 `image_url`（`data:{mime};base64,...`），与文本提示一起提交
- 采样：`temperature=0.1`，追求稳定输出
- 提示工程：两套 system prompt 分别面向「一餐多菜识别」与「营养标签 OCR」，**要求严格输出 JSON**，且明确指示「只做识别与估重，不要输出热量或营养成分」
- 输出解析：`providers/client.py:extract_json` 容忍 Markdown 围栏与尾随内容（用 `JSONDecoder.raw_decode` 取第一个完整 JSON 对象），解析失败降级为警告而非 500

实现见 [providers/client.py](file:///Users/philipclaw/Downloads/padow-ai/Week12/FoodLens/backend/app/providers/client.py) 与 [providers/vision.py](file:///Users/philipclaw/Downloads/padow-ai/Week12/FoodLens/backend/app/providers/vision.py)。

`VISION_PROVIDER` 支持三种取值：

| 取值 | 用途 |
| --- | --- |
| `demo` | **零 Key** 离线跑通全链路：按分隔符（`、，,/\s`）切分文字输入当作「识别结果」，用于开发与测试 |
| `openai` | 真实 VLM（OpenAI 兼容多模态端点，本项目默认） |
| `qwen` | 预留：DashScope 原生 SDK |

### 营养数据 API（检索层的数据源）

**调用的是 USDA FoodData Central API（`data.gov` 上的权威开放数据集）。**

- 端点：`https://api.nal.usda.gov/fdc/v1/foods/search`
- 数据集：`Foundation` + `SR Legacy`（美国农业部官方食物成分库）
- 使用方式：**离线摄入**——由 `scripts/ingest_usda.py` 在构建期一次性拉取并落盘为 `natural_usda.json`，**运行时零网络依赖**
- 许可：公共领域（Public domain），可自由使用

> 规划中（v1.2）：接入 **Open Food Facts**（ODbL）补充包装食品条码数据。

## 产品架构

### 分层架构

```text
[手机浏览器]
   │  拍照 / 上传图片 + 可选文字线索
   ▼
React 前端  ── 菜品卡片(可增删) · 克数滑杆 · 双语切换 · 实时重算
   │  HTTP（analyze / recompute）
   ▼
FastAPI 后端
   ├─ 感知层 (VLM)    : Qwen3-VL-Plus 识别每道菜(中英名)+粗分量；营养标签 OCR
   ├─ 归一/检索层      : 菜名 → 营养库匹配（精确 → 别名 → 模糊）
   ├─ 计算层          : 库中「每100g」× 克数 → 分项 / 总量 / 三大营养素（纯确定性）
   └─ 编排层          : 轻度修正（增删菜品、调量、重算，不触 VLM）
   │
   ▼
营养数据库（本地 JSON 索引，手工优先）
   ├─ L1 天然食物  natural_usda.json  ← USDA 脚本生成
   ├─ L2 中餐菜肴  seed.json          ← 自建（手工 + LLM 辅助校对）
   └─ L3 包装食品  (规划中)            ← Open Food Facts
```

### 请求时序

**首次分析 `POST /api/analyze`：**

```text
前端 ──图片/文字──▶ 感知层(VLM) ──菜品[{名,克数,置信度}]──▶ 检索层(营养库)
                                                              │  命中 record
                                                              ▼
                                                          计算层(克数×每100g)
                                                              │
前端 ◀──items[] + totals + warnings ────────────────────── 编排层
```

**用户修正 `POST /api/recompute`（不调用 VLM）：**

```text
前端 ──[逐条 {id 或 name, grams}]──▶ 检索层 → 计算层 → 总量
```

这条链路是整个设计的价值所在：**一次识别，多次免费重算**。改克数只走检索 + 乘法，毫秒级返回，且结果必然与库数据一致。

### 关键设计决策

| 决策 | 原因 |
| --- | --- |
| VLM 只输出「菜名 + 克数 + 置信度」 | 数值交给数据库，消除幻觉 |
| 名 → 库匹配：精确 → 别名 → 模糊（阈值 0.72） | 容忍别名、简繁与口语差异；子串加成加长度约束，避免「量子纠缠炒饭」被别名「炒饭」误命中 |
| 分量护栏 `[0.25×, 4×]` 标准份 | 拦截明显离谱的估重，且标注「已校准」保持透明 |
| 未命中即 `unmatched` 且计 0 | 宁可说「不知道」，也不编造热量 |
| 数据分层 + 手工优先 | 脚本生成的 USDA 数据不会覆盖人工维护的中餐库 |
| `usda_` 前缀命名 | 脚本产物与手工 id 结构上隔离，避免冲突 |

## 如何用开放数据提升热量计算精度

「热量算得准不准」的瓶颈通常不在视觉，而在**营养数值从哪来**。FoodLens 的做法是把权威开放数据接进检索层。

**1. 问题**

- 让 VLM「看图估热量」→ 数值随意、不可复现、无法审计。
- 自建中餐库覆盖面有限 → 天然食物（三文鱼、西兰花、牛油果……）无处可查。

**2. 接入 USDA（data.gov）开放数据**

`scripts/ingest_usda.py` 面向 138 项高频天然食物（米面薯类、肉禽蛋、水产、奶豆、蔬果、坚果、油脂、调味、饮品），从 USDA FoodData Central 拉取官方成分数据：

```bash
cd backend
uv run python scripts/ingest_usda.py     # 生成 natural_usda.json + _meta.json
```

- **构建期一次性拉取**，运行时零网络依赖，复现性好；
- 数据来源可追溯：`_meta.json` 记录来源、条数、数据类型分布与生成时间；
- 原始响应缓存在 `backend/data/usda/`（已 gitignore），限流约 1.1s/请求。

**3. 精度上的三道关卡**

| 关卡 | 处理 |
| --- | --- |
| **单位口径统一** | USDA 能量字段有三种口径：Foundation `2047`/`2048`（`unitName=KCAL`，直接取值）、`1008`（经典 kcal）、SR Legacy `1062`（`unitName=kJ`，**÷4.184 换算**）。脚本按优先级取值并统一为 kcal，蛋白 `1003` / 脂肪 `1004` / 碳水 `1005` 统一为克 |
| **选条准确性** | 同一查询会返回多个候选（如 `Broccoli` vs `Broccoli raab`）。脚本用「查询词全覆盖 + 多余词只能是已知修饰词（品名/部位/加工方式）」的严格过滤，再按 Foundation 优先 + 描述最短排序，避免选到「同科异种」 |
| **运行时数据隔离** | 生成文件带 `source: "usda"`；与手工库同名时**手工优先**并打印告警，脚本产物不会覆盖人工条目 |

**4. 效果**

- 命中天然食物时，热量 = 库值 × 克数，**精确、可复现、可解释**（前端显示来源标签 `USDA`）。
- 营养库从 34 条 → **224 条**（L2 自建 86 + L1 USDA 138），显著扩大可识别范围。
- 评估集回归守护精度不退化（见 [测试与评估](#测试与评估)）。

## 目录结构

```text
FoodLens/
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI 入口：/api/analyze、/api/recompute、/api/health
│   │   ├── orchestrator.py    # 管线编排：感知 → 匹配 → 计算
│   │   ├── compute.py         # 营养计算 + 分量护栏
│   │   ├── schemas.py         # 请求/响应数据模型（API 契约）
│   │   ├── config.py          # 从 .env 读取配置
│   │   ├── nutrition/
│   │   │   ├── database.py    # 加载 / 索引 / 匹配
│   │   │   └── data/
│   │   │       ├── seed.json          # 手工营养库（中餐菜肴 + 常见食物）
│   │   │       ├── natural_usda.json  # USDA 天然食物（脚本生成，勿手改）
│   │   │       └── _meta.json         # 数据来源元信息
│   │   └── providers/
│   │       ├── client.py      # OpenAI 兼容多模态 HTTP 客户端 + JSON 解析
│   │       └── vision.py      # 感知层：菜品识别 / 标签 OCR / demo
│   ├── scripts/
│   │   └── ingest_usda.py     # 离线摄入 USDA 天然食物 → natural_usda.json
│   ├── eval/
│   │   └── cases.json         # 评估集（回归用例）
│   └── tests/                 # pytest 单元测试 + 评估集回归
├── frontend/                  # React + TypeScript + Vite
│   └── src/
│       ├── App.tsx            # 主界面与交互
│       ├── api.ts / types.ts  # API 客户端与契约类型
│       ├── copy.ts            # 中英文案
│       ├── components/ItemCard.tsx
│       └── food.css
├── docs/superpowers/
│   ├── specs/                 # 设计文档
│   └── plans/                 # 实施计划
└── README.md
```

## 快速开始

### 方式一：Demo 模式（无需任何 API Key）

后端默认可用 `demo` 适配器，用文字输入即可跑通全链路。

终端 1 —— 后端：

```bash
cd backend
uv sync
VISION_PROVIDER=demo uv run uvicorn app.main:app --host 127.0.0.1 --port 8200
```

终端 2 —— 前端：

```bash
cd frontend
npm install
npm run dev
```

浏览器打开 `http://localhost:5180`，在「补充描述」里输入例如 `红烧肉、白米饭、清炒时蔬`，点击「分析」。

### 方式二：接入真实视觉模型

```bash
cd backend
cp .env.example .env   # 填入你的 Key
```

`.env` 关键配置：

```dotenv
VISION_PROVIDER=openai
LLM_API_KEY=your-key
LLM_BASE_URL=https://your-endpoint/compatible-mode/v1
LLM_MODEL=qwen3-vl-plus
CORS_ORIGINS=http://localhost:5180,https://*.trycloudflare.com

# 仅离线摄入脚本使用，运行时不读；申请：https://fdc.nal.usda.gov/api-key-signup.html
USDA_API_KEY=
```

重启后端即可。手机端通过 `npm run dev -- --host` 暴露的局域网地址访问，或用任意隧道（CORS 已放行 `*.trycloudflare.com`）。

> **安全提示**：`backend/.env` 已被 `.gitignore` 排除，请勿提交真实 Key。

> 关闭与重启的完整命令见 [docs/local-dev.md](file:///Users/philipclaw/Downloads/padow-ai/Week12/FoodLens/docs/local-dev.md)。

## 环境变量

`backend/.env`（由 `config.py:Settings` 读取；`USDA_API_KEY` 仅摄入脚本读取）：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `ENVIRONMENT` | `development` | 运行环境标识 |
| `VISION_PROVIDER` | `demo` | `demo` / `openai` / `qwen` |
| `LLM_API_KEY` | 空 | OpenAI 兼容多模态端点的 Key |
| `LLM_BASE_URL` | 空 | 形如 `https://<endpoint>/compatible-mode/v1` |
| `LLM_MODEL` | `qwen3-vl-plus` | 视觉模型名 |
| `CORS_ORIGINS` | `http://localhost:5180` | 逗号分隔的允许来源 |
| `REQUEST_TIMEOUT_SECONDS` | `60` | VLM 请求超时（秒） |
| `USDA_API_KEY` | 空 | **仅** `scripts/ingest_usda.py` 使用，运行时不读 |
| `DASHSCOPE_API_KEY` / `DASHSCOPE_BASE_URL` / `QWEN_MODEL` | 空 | 预留（`provider=qwen`） |

## API 参考

### `POST /api/analyze`

`multipart/form-data` 表单字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `image` | file | 一餐照片或营养标签照片，可空 |
| `text` | string | 可选文字线索（如菜名） |
| `mode` | string | `meal`（默认）或 `label` |
| `locale` | string | `zh-CN` 或 `en` |

返回 `AnalyzeResponse`：

```json
{
  "mode": "meal",
  "items": [
    {
      "name": {"zh": "红烧肉", "en": "Braised Pork Belly"},
      "matched_id": "hongshaorou",
      "grams": 150.0,
      "kcal": 708.0, "protein": 18.0, "fat": 67.5, "carb": 4.5,
      "confidence": 0.9,
      "status": "matched",
      "note": null,
      "source": "seed"
    }
  ],
  "totals": {"kcal": 708.0, "protein": 18.0, "fat": 67.5, "carb": 4.5},
  "warnings": []
}
```

`status` 取值：`matched`（命中库） / `unmatched`（未收录，热量计 0） / `estimated`（用户新增待匹配）。
`source` 取值：`seed`（手工库） / `usda`（USDA 生成） / `manual`（用户手工录入） / `null`（未命中）。

### `POST /api/recompute`

仅重算，**不调用 VLM**。用于用户改克数或增删菜品：

```json
{
  "locale": "zh-CN",
  "items": [
    {"id": "hongshaorou", "grams": 300},
    {"name_zh": "宫保鸡丁", "grams": 100}
  ]
}
```

条目可给 `id`（优先）或 `name_zh` / `name_en`。返回结构同上 `AnalyzeResponse`。

### `GET /api/health`

```json
{"provider": "openai", "status": "ok", "nutrition_count": 224}
```

## 营养库与数据维护

营养库按来源分层加载，运行时按**手工优先**解析：

| 层 | 文件 | 来源 | 维护方式 |
| --- | --- | --- | --- |
| L1 天然食物 | `natural_usda.json` | USDA FoodData Central | 脚本生成，**勿手改** |
| L2 中餐菜肴 / 常见食物 | `seed.json` | 自建（手工 + LLM 辅助校对） | 手工编辑 |
| L3 包装食品 | 规划中 | Open Food Facts | v1.2 接入 |

### L2：手工库 `seed.json`

在 `backend/app/nutrition/data/seed.json` 中新增条目：

```json
{
  "id": "hongshaorou",
  "name": {"zh": "红烧肉", "en": "Braised Pork Belly"},
  "aliases": ["红烧五花肉", "Braised Pork"],
  "category": "dish",
  "per_100g": {"kcal": 472, "protein": 12.0, "fat": 45.0, "carb": 3.0},
  "standard_servings": [
    {"label": {"zh": "一份(约150g)", "en": "1 serving (~150g)"}, "grams": 150}
  ],
  "source": "seed"
}
```

### L1：从 USDA 离线摄入

```bash
cd backend
# 首次需要 USDA API Key（免费申请：https://fdc.nal.usda.gov/api-key-signup.html）
USDA_API_KEY=your-key uv run python scripts/ingest_usda.py

# 常用参数
uv run python scripts/ingest_usda.py --check            # 只比对差异，不写盘
uv run python scripts/ingest_usda.py --recompute-meta   # 仅重建 _meta.json
uv run python scripts/ingest_usda.py --refresh          # 忽略本地缓存，重新拉取
```

扩展映射表：编辑脚本内 `USDA_FOODS`（`FoodSpec(query, id, zh, en, category, aliases, serving_grams, ...)`），id 统一 `usda_` 前缀以避免与手工库冲突。Key 读取顺序：`--api-key` > 环境变量 `USDA_API_KEY` > `.env` > `DEMO_KEY`；单条失败不中断整批。

> **注意**：修改数据文件后需重启后端（营养库加载有 `lru_cache`）。

## 测试与评估

```bash
cd backend
uv run pytest -q
```

覆盖：JSON 解析健壮性、营养库精确/别名/模糊匹配、营养计算与分量护栏、编排管线（demo 模式，不触网络）。

**评估集回归**：`backend/eval/cases.json` 存放典型用例（菜名 → 期望命中条目 + 期望热量），`tests/test_eval.py` 在 demo 模式下跑全链路并断言**命中率 ≥ 90%**、**总热量相对误差 ≤ 15%**。新增食物后跑一次 `uv run pytest -q` 即可发现回归。

## 部署

前端 dev server 已放行 `*.trycloudflare.com`（`vite.config.ts` 的 `allowedHosts`），后端 CORS 亦放行该域名正则。用 cloudflared quick tunnel 即可对外分享：

```bash
# 终端 1：后端
cd backend && uv run uvicorn app.main:app --host 127.0.0.1 --port 8200

# 终端 2：前端
cd frontend && npm run dev

# 终端 3：隧道
cloudflared tunnel --url http://localhost:5180
```

隧道会打印一个 `https://<随机>.trycloudflare.com` 地址，手机浏览器直接打开即可。

## 已知局限

- **准确度**：已建立评估集（`eval/cases.json` + `tests/test_eval.py`），约束**命中率 ≥ 90%**、**热量误差 ≤ 15%**。但评估集规模有限，且单图估克数本身存在不确定性，当前策略为「VLM 直接估克数 + 护栏 + 用户修正」。
- **中餐差异大**：同一道菜做法差异可达数倍，自建库以「常见做法均值」入库，结果为估算性质。
- **不做**：用户账户 / 云同步、原生 App、条形码扫描、多图/视频、社区分享。

## 数据来源与许可

- **L2 手工库**（`seed.json`）：项目自建，以常见做法均值入库。
- **L1 天然食物**（`natural_usda.json`）：来自 [USDA FoodData Central](https://fdc.nal.usda.gov/)（`data.gov` 开放数据集），属**公共领域（Public domain）**，可自由使用。生成文件 `_meta.json` 记录了来源、条数、数据类型分布与生成时间，可追溯。
- **L3（规划中）**：Open Food Facts，ODbL 许可。

## 版本

- **v1.0**：多菜品识别 + 营养标签 OCR + 确定性计算 + 轻量修正 + 中英双语。
- **v1.0.1（营养数据层扩充，方案 A）**：USDA 离线摄入（+138 条）、中餐菜肴扩至 86 条、数据来源可视化、评估集与回归测试。设计见 [docs/superpowers/specs/2026-11-19-foodlens-nutrition-data-design.md](file:///Users/philipclaw/Downloads/padow-ai/Week12/FoodLens/docs/superpowers/specs/2026-11-19-foodlens-nutrition-data-design.md)。
