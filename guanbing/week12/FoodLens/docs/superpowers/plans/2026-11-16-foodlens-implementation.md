# FoodLens v1.0 实现计划

依据设计文档 [2026-11-16-foodlens-design.md](../specs/2026-11-16-foodlens-design.md) 交付 v1.0。

## 技术栈

- 后端：Python 3.11+、FastAPI、pydantic v2、uv 管理依赖
- 前端：React 18 + TypeScript + Vite
- 视觉：OpenAI 兼容 / Qwen 多模态（默认 `qwen3-vl-plus`），复用 UrbanUnbox 的 `.env`

## API 契约（冻结）

### 数据模型

```text
LocalizedText      { zh: str, en: str }
NutritionPer100g   { kcal: float, protein: float, fat: float, carb: float }
StandardServing    { label: LocalizedText, grams: float }
FoodRecord         { id, name: LocalizedText, aliases: str[], category, per_100g, standard_servings: StandardServing[], source }
AnalyzedItem       { name: LocalizedText, matched_id: str|null, grams: float,
                     kcal/protein/fat/carb: float, confidence: float,
                     status: "matched"|"unmatched"|"estimated", note: LocalizedText|null }
Totals             { kcal, protein, fat, carb }
AnalyzeResponse    { mode: "meal"|"label", items: AnalyzedItem[], totals: Totals, warnings: LocalizedText[] }
```

### 端点

- `POST /api/analyze`（multipart）
  - 字段：`image`(file，可空)、`text`(str，可空)、`mode`("meal"|"label")、`locale`("zh-CN"|"en")
  - 返回：`AnalyzeResponse`
- `POST /api/recompute`（json）
  - 体：`{ items: [{ id?: str, name_zh?: str, name_en?: str, grams: float }], locale }`
  - 返回：`AnalyzeResponse`（仅重算，不调用 VLM）
- `GET /api/health` → `{ provider, status, nutrition_count }`

## 模块与文件

```text
backend/app/
├── config.py            # 读取 .env
├── schemas.py           # 上述数据模型
├── nutrition/
│   ├── database.py      # 加载/索引/匹配（精确→别名→模糊）
│   └── data/*.json      # 种子库（中餐 + 西餐/包装）
├── providers/
│   ├── vision.py        # demo | openai | qwen：菜品识别 + 克数
│   └── label_ocr.py     # 营养标签 OCR
├── compute.py           # 营养计算 + 分量护栏
├── orchestrator.py      # 管线编排
└── main.py              # FastAPI 应用与端点
frontend/src/
├── App.tsx / api.ts / types.ts / copy.ts / food.css
```

## 实施顺序

1. 脚手架 + 复用 `.env`
2. 数据层（schemas + 种子营养库 + 匹配）
3. 计算层 + 分量护栏
4. 感知层（vision + label OCR）
5. 编排 + API
6. 前端（卡片 / 滑杆 / 增删 / 双语 / 实时重算）
7. 测试 + 评估脚手架
8. README + 端到端验证

## 验收标准

- 无 Key 时 `demo` 模式可跑通全链
- 真实模式：上传一张多菜图 → 返回每道菜名称、克数、分项与总量三大营养素
- `/api/recompute`：改克数无需重调 VLM 即可重算
- 所有面向用户的文本与 LLM 输出均含中英双语
- 后端单元测试通过（计算、匹配、护栏、归一）
