# FoodLens 营养数据层扩充设计（方案 A · 轻量补齐）

依据 [2026-11-16-foodlens-design.md](2026-11-16-foodlens-design.md) 的架构分层，本期在不改动运行时链路的前提下扩充营养库。

## 1. 目标与范围

v1.0 种子库仅 34 条手写记录，覆盖窄。本期目标：

- 把营养库扩充到**高频天然食物 + 常见中餐菜肴**，显著提升 VLM 输出菜名的命中率。
- 所有数据**可复现、可追溯**：每条记录标明来源（`source`），前端可见。
- **不引入运行时网络依赖**：数据在构建期一次性摄入落盘，运行时仍是本地索引 + 匹配。

范围（用户确认的方案 A）：USDA 自动摄入天然食物 + 手工/LLM 辅助录入中餐菜肴。

**不做**：Open Food Facts 包装食品（ODbL，留待 v1.2）、中国食物成分表全量分发、条形码扫描。

## 2. 数据源事实（已实测，非推测）

### USDA FoodData Central

```text
GET https://api.nal.usda.gov/fdc/v1/foods/search
    ?query=<英文名>&dataType=Foundation,SR%20Legacy&pageSize=N&api_key=<KEY>
```

- 免费 key：data.gov 自助申请；限流 1000 请求/小时。`DEMO_KEY` 仅 30/小时，够验证不够建库。
- 返回 `foods[].foodNutrients[]`，需按 `nutrientId` 取值。
- **能量字段不统一（实测三种情形）**：

| dataType | nutrientId | unitName | 处理 |
| --- | --- | --- | --- |
| Foundation | `2047` / `2048` | `KCAL` | 直接取（优先 2047） |
| SR Legacy | `1062` | `kJ` | ÷ 4.184 |
| 经典 | `1008` | `kcal` | 直接取 |

- 蛋白 `1003`、脂肪 `1004`、碳水 `1005`，单位均为 `G`。
- **无中文名** → 需自建「英文查询名 → 中文名/别名」映射表。

### Open Food Facts（本期不接入，仅记录）

```text
GET https://world.openfoodfacts.org/api/v2/product/{barcode}.json?fields=product_name,nutriments
```

`nutriments` 直接提供 `energy-kcal_100g` / `proteins_100g` / `fat_100g` / `carbohydrates_100g`，天然按每 100g。许可为 ODbL（传染性，需署名）。v1.2 接入时须在 README 标注 "Contains data from Open Food Facts, available under ODbL"。

## 3. 数据分层与文件布局

| 层 | 内容 | 来源 | 文件 |
| --- | --- | --- | --- |
| L1 天然食物 | 苹果、鸡蛋、牛奶、白米饭、菠菜… | USDA FDC（公共领域） | `natural_usda.json`（脚本生成） |
| L2 中餐菜肴 | 红烧肉、宫保鸡丁… | 手工 + LLM 辅助校对 | `seed.json`（受保护） |
| L3 包装食品 | — | v1.2 接 OFF | — |

```text
backend/
├── scripts/
│   └── ingest_usda.py          # 离线摄入脚本
├── app/nutrition/data/
│   ├── seed.json               # 手工条目：中餐菜肴（脚本永不改写）
│   ├── natural_usda.json       # 脚本生成：USDA 天然食物
│   └── _meta.json              # 生成元数据：来源 / 日期 / 条数
└── data/                       # 原始响应缓存（.gitignore）
```

**数据优先级**：`manual`（手工）> `usda`（脚本生成）。去重以 `id` 为准，脚本仅写自己那一个文件，从结构上杜绝覆盖手工条目。

## 4. 摄入脚本设计（`scripts/ingest_usda.py`）

```bash
uv run python scripts/ingest_usda.py            # 拉取并写入 natural_usda.json
uv run python scripts/ingest_usda.py --check    # 只比对差异，不写盘
uv run python scripts/ingest_usda.py --recompute-meta  # 仅重建 _meta.json
```

- **输入**：内置 `USDA_FOODS` 映射表（英文查询名 → `{zh, en, aliases, category}`），首期约 120~150 条高频食物（主食 / 蔬果 / 肉蛋奶 / 豆制品 / 坚果）。
- **流程**：逐条调 USDA search（`Foundation,SR Legacy`，Foundation 优先）→ 取首条 → 归一化为 `FoodRecord` → 汇总写盘。
- **归一**：按上表映射 kcal/蛋白/脂肪/碳水；能量若仅以 kJ 给出则换算为 kcal。
- **幂等**：重复运行产出稳定（结果按 id 排序）。
- **容错**：单条失败记入日志并跳过，不中断整批；结束时打印成功/失败统计。
- **限流**：内置间隔（≥ 1s/请求）避免触碰 1000/h。

## 5. schema 扩展（向后兼容）

- `FoodRecord.source` 规范取值：`seed | usda | china_fct | manual`（字段已存在，仅规范取值）。
- `FoodRecord` 新增可选 `fdc_id: str | None = None`，用于溯源。
- `AnalyzedItem` 新增可选 `source: str | None = None`，供前端展示来源。
- **API 契约不变**（仅新增可选字段），前端可忽略。

## 6. 数据来源可视化

- 后端：`compute.make_item` 透传 `record.source` 到 `AnalyzedItem.source`。
- 前端：`ItemCard` 底部增加来源小标签，文案入 `copy.ts`：

  | source | 中文 | English |
  | --- | --- | --- |
  | `seed` / `manual` | 自建库 | Built-in |
  | `usda` | USDA | USDA |
  | `china_fct` | 中国食物成分表 | China FCT |

## 7. 评估集与回归测试

- `backend/eval/cases.json`：若干条 `{ id, input_text, expect_items: [菜名...], expect_kcal, tolerance }`。
- `backend/tests/test_eval.py`：
  - **菜名命中率** = 期望菜品中被库命中的比例。
  - **热量相对误差** = `|实际 − 期望| / 期望`，汇总 MAE 与超 15% 的用例数。
  - 用 demo provider 跑（不触网络），保证 CI 可离线执行。
- 门槛（写入测试断言）：命中率 ≥ 90%，热量相对误差 ≤ 15%。

## 8. 中餐菜肴扩充流程

1. 由 LLM 按「常见做法均值」生成候选条目（名称、别名、每 100g 营养、标准份）。
2. 整理为待审表格交用户确认；用户修订后写入 `seed.json`。
3. 每条标注估算性质；不为"精准"作虚假背书。

## 9. 实施顺序

1. schema 扩展（`source` 规范化、`fdc_id`、`AnalyzedItem.source`）
2. 前端来源标签（`ItemCard` + `copy.ts`）
3. `ingest_usda.py` + 食物映射表 → 生成 `natural_usda.json`
4. 中餐菜肴候选生成与审核 → 扩充 `seed.json`
5. 评估集 + 回归测试
6. README 更新（数据来源与许可）+ 提交

## 10. 验收标准

- `uv run pytest -q` 全绿（含评估集门槛）。
- 无 Key 时 demo 全链可跑通。
- 天然食物命中率显著提升；`/api/health` 的 `nutrition_count` 大幅增加。
- 每条入库记录均可追溯到来源，前端可见。
- 摄入脚本可一键重跑，且**不会覆盖**手工条目。

## 11. 风险与开放问题

- **英文名 → 中文映射是主要工作量**：首期只做高频食物，宁可少而准，不做机器直译的批量灌入。
- **菜肴精度天花板**：同一道菜做法差异可达数倍，库内为均值；UI 必须保持"估算"语义，不承诺精确。
- **USDA 数值口径差异**：Foundation 与 SR Legacy 对同一食物数值可能不同，本期统一「Foundation 优先」，并在 `_meta.json` 记录实际取用来源。
- **版权**：本期不使用《中国食物成分表》OCR 数据集（其仓库无 license 且原书受版权保护）；`china_fct` 取值仅为未来手工录入预留。
