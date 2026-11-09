# 测试说明（TESTING.md）· 深度研究助手

本项目测试分**三层**，成本从零到低逐级递进，建议按顺序执行：

| 层级 | 内容 | 触网 | 成本 | 耗时 | 对应里程碑 |
| --- | --- | --- | --- | --- | --- |
| ① 单元测试 | pytest + mock，验证核心逻辑 | ❌ | 0 | < 2 秒 | M1~M4 逻辑正确性 |
| ② 单步联调 | 真实调 1 次 LLM / 1 次搜索 | ✅ | 极低 | < 30 秒 | M1 |
| ③ 端到端冒烟 | CLI 跑完整研究流程 + 验收脚本 | ✅ | 低（约 1~3 分钟用量） | 1~3 分钟 | M5 / PRD 第 7 节 |

---

## 1. 环境准备（一次性）

```bash
# 进入项目根目录
cd 综合案例-02-diy

# 1) 安装依赖（Python 3.12）
python3 -m pip install -r requirements.txt

# 2) 配置密钥：复制示例并填入真实值
cp .env.example .env
#   DASHSCOPE_API_KEY  ← 阿里云百炼 https://bailian.console.aliyun.com
#   BOCHA_API_KEY      ← 博查 https://open.bochaai.com
```

验证环境就绪（两条都应打印 OK）：

```bash
python3 -c "import openai, httpx, pydantic, dotenv, pytest; print('依赖 OK')"
python3 -c "from research import config; config.require_keys(); print('密钥 OK')"
```

> `.env` 已被 `.gitignore` 排除，密钥不会入库。

---

## 2. 第一层：单元测试（不触网、零成本）

```bash
python3 -m pytest tests/ -v
```

预期输出（14 个用例全绿）：

```
tests/test_agent.py::test_agent_loop_and_outputs PASSED
tests/test_agent.py::test_agent_terminates_on_round_budget PASSED
tests/test_registry.py::test_register_assigns_sequential_sids PASSED
tests/test_registry.py::test_register_dedup_ignores_tracking_params_and_case PASSED
tests/test_registry.py::test_register_rejects_invalid_url PASSED
tests/test_registry.py::test_add_finding_filters_unknown_sids PASSED
tests/test_registry.py::test_finding_without_source_is_inference PASSED
tests/test_registry.py::test_findings_by_sub PASSED
tests/test_registry.py::test_refs_string_tolerated PASSED
tests/test_registry.py::test_latest_date PASSED
tests/test_search.py::test_parse_response PASSED
tests/test_search.py::test_web_search_retries_then_succeeds PASSED
tests/test_search.py::test_web_search_raises_after_retries PASSED
tests/test_search.py::test_web_search_requires_key PASSED

14 passed in ~1s
```

### 测试点与被测逻辑对照

| 文件 | 测试点 | 保障的能力 |
| --- | --- | --- |
| `test_registry.py` | sid 顺序分配；URL 去重（忽略 `utm_*`/大小写/fragment）；非法 URL 拒绝 | 同一网页不重复编号、不重复阅读 |
| | 材料引用中不存在的 sid 被剔除、全部非法则丢弃 | **防幻觉引用**（结论可追溯的底线） |
| | `source_ids` 为空 → `is_inference` | "模型推断"标注机制 |
| | refs 传入 `"S1, S2"` 字符串自动拆为列表 | 容错 LLM 输出格式（真实冒烟踩过的坑） |
| `test_search.py` | 博查响应解析（name/url/summary/datePublished） | 工具层解析正确 |
| | 前置失败 + 第二次成功 → 重试生效；连败抛 `SearchError` | 搜索失败不中断整轮 |
| `test_agent.py` | 反思 need_more → 下一轮继续，resolved → 终止；2 轮结束全部 resolved | **多轮主循环状态机**（核心） |
| | `MAX_ROUNDS` 到限后 need_more 强制转 abandoned | 轮次预算兜底，不会无限迭代 |
| | 落盘断言：`（模型推断` 标注、`S999` 被剔除、来源时间覆盖 `info_cutoff`、trace 首尾事件 | 四件套渲染兜底逻辑 |

### mock 机制说明（写新测试时照抄这个模式）

单测通过 `monkeypatch` 替换模块级依赖，全程不触网：

```python
# 1) mock 搜索工具：替换 agent 模块里导入的 web_search
from research import agent as agent_mod
monkeypatch.setattr(
    agent_mod, "web_search",
    lambda query, **kw: [SearchHit(url=f"https://example.com/{query}",
                                   title=f"结果{query}", summary="材料")],
)

# 2) mock LLM：替换 agent 模块里导入的 chat_json，按 schema 类型分发假返回
def fake_chat_json(system, user, schema, model=None, **kwargs):
    if schema is Plan:       return Plan(subquestions=[{"id": "Q1", "question": "..."}])
    if schema is Keywords:   return Keywords(queries=["测试关键词"])
    if schema is Extraction: return Extraction(findings=[{"content": "材料", "source_ids": ["S1"]}])
    if schema is Judgement:  return Judgement(status="need_more")   # 依次给出预设状态即可驱动主循环
    if schema is Report:     return Report(title="T", summary="S", ...)
monkeypatch.setattr(agent_mod, "chat_json", fake_chat_json)

# 3) 用 tmp_path 隔离产物目录，不污染真实 output/
agent = ResearchAgent("测试主题", out_dir=tmp_path / "out")
```

### 常用 pytest 参数

```bash
python3 -m pytest tests/test_registry.py -v      # 只跑一个文件
python3 -m pytest tests/ -k "dedup or inference" -v  # 按用例名筛选
python3 -m pytest tests/ --tb=short              # 失败时输出简短堆栈
python3 -m pytest tests/ -q                      # 安静模式（日常回归）
```

> 注：`llm.py` 的墙钟硬期限（`CREATE_DEADLINE_SEC=180`）依赖真实网络挂起才能复现，不在单测覆盖内，由第②③层联调保障。

---

## 3. 第二层：单步联调验证（真实 API、小成本）

分别用**一次**真实调用验证 LLM 适配层和搜索工具（对应 M1 验收）。

**验证 S3 —— LLM 返回可校验的 JSON：**

```bash
python3 - <<'EOF'
from research import config
config.require_keys()
from research.llm import chat_json
from research.models import Plan

plan = chat_json(
    '你是研究规划师。把研究主题拆成 3~4 个子问题。'
    '只输出 JSON：{"subquestions": [{"id": "Q1", "question": "..."}]}',
    "研究主题：AI 眼镜 2026 年竞争格局",
    Plan, model="qwen-flash",
)
print("LLM OK，子问题数:", len(plan.subquestions))
EOF
```

预期输出：`LLM OK，子问题数: 3`（或 3~4 之间）。

**验证 S4 —— 博查搜索：**

```bash
python3 - <<'EOF'
from research import config
config.require_keys()
from research.tools.search import web_search

hits = web_search("天空为什么是蓝色的", count=5)
print("Search OK，结果数:", len(hits))
print("样例:", hits[0].title, "|", hits[0].url)
EOF
```

预期输出：`Search OK，结果数: 5` + 一条真实网页结果。

---

## 4. 第三层：端到端冒烟（真实 API，约 1~3 分钟）

```bash
python3 -m research "AI 眼镜 2026 年竞争格局" --max-rounds 2
```

CLI 参数：

| 参数 | 说明 | 默认 |
| --- | --- | --- |
| `topic`（位置参数） | 研究主题，必填 | — |
| `--max-rounds N` | 限制检索-反思轮次，**冒烟建议 2，控成本** | 4 |
| `--out DIR` | 输出根目录 | `output/` |
| `--quiet` | 不打印过程进度 | 打印 |

跑完会得到：

```
===== 研究完成 =====
输出目录：output/20260909-230546_AI-眼镜-2026-年竞争格局
迭代轮次：1｜来源：25｜材料：14｜耗时：60 秒
关键结论：4 条，带来源 4 条（100%）
```

产物两件：

```
output/<时间戳>_<主题>/
├── report.md    # ①报告（摘要/正文/关键结论/遗留问题）②来源列表 ④置信度说明
└── trace.jsonl  # ③研究过程记录（逐事件 JSONL，可回放）
```

---

## 5. 冒烟产物验收脚本（对应 PRD 第 7 节）

冒烟跑完后立即执行，自动核对三条验收标准（结论带源率 ≥80%、四件套结构完整、过程可回放）：

```bash
python3 - <<'EOF'
import json, re, sys
from collections import Counter
from pathlib import Path

out_dir = sorted(Path("output").iterdir())[-1]          # 取最新一次产出
report = (out_dir / "report.md").read_text(encoding="utf-8")
trace = [json.loads(l) for l in (out_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
print("检查目录：", out_dir)
ok = True

# 验收 1：四件套结构完整
for sec in ["## 摘要", "## 正文", "## 关键结论", "## 遗留问题", "## 置信度说明", "## 来源列表"]:
    if sec not in report:
        print(f"❌ 缺少章节：{sec}"); ok = False

# 验收 2：关键结论带源率 >= 80%（"（模型推断" 开头的即无源）
body = report[report.index("## 关键结论"):report.index("## 遗留问题")]
conclusions = re.findall(r"^\d+\..+", body, re.M)
with_src = [c for c in conclusions if "（模型推断" not in c]
rate = len(with_src) / len(conclusions) if conclusions else 0.0
print(f"关键结论 {len(conclusions)} 条，带源 {len(with_src)} 条（{rate:.0%}）",
      "✅" if not conclusions or rate >= 0.8 else "❌")
ok = ok and (not conclusions or rate >= 0.8)

# 验收 3：过程记录可还原（关键词/页面/轮次）
actions = Counter(e["action"] for e in trace)
print("trace 事件：", dict(actions))
print("检索关键词：", [e["detail"]["query"] for e in trace if e["action"] == "search"])
replayable = {"plan", "search", "read", "reflect", "synthesize"} <= set(actions)
print("过程可回放：", "✅" if replayable else "❌")
ok = ok and replayable

print("\n验收结果：", "通过 ✅" if ok else "未通过 ❌")
sys.exit(0 if ok else 1)
EOF
```

预期输出（以 2026-09-09 真实冒烟为例）：

```
关键结论 4 条，带源 4 条（100%） ✅
trace 事件： {'plan': 1, 'search': 5, 'read': 5, 'reflect': 5, 'synthesize': 1}
过程可回放： ✅

验收结果： 通过 ✅
```

手动抽查两处（可选）：

```bash
# 看报告的关键结论与来源标注
sed -n '/## 关键结论/,/## 遗留问题/p' output/*/report.md
# 看 trace 里某一轮的反思判定
grep reflect output/*/trace.jsonl | tail -2
```

---

## 6. 排错速查

| 现象 | 原因与处理 |
| --- | --- |
| `缺少环境变量：DASHSCOPE_API_KEY` | `.env` 未配置或不在项目根目录 |
| 单测跑了很久 / 产生 `output/` 文件 | 不应发生——单测全部 mock 且用 `tmp_path`；确认用的是 `pytest tests/` 而非误跑真实脚本 |
| 冒烟卡在某一步很久 | `chat_json` 有 180 秒墙钟硬期限，超时自动重试/降级；若仍卡死可 `Ctrl+C`（CLI 已捕获并退出） |
| 报告出现"（模型推断｜无直接来源，建议核实）" | 正常：该结论无来源支撑，渲染层已按 PRD 要求强制标注 |
| `output/` 下只有 `trace.jsonl` 没有 `report.md` | 该次运行被中途终止（如超时被杀），属于中断残留，可删除 |
| 搜索/LLM 偶发 429 或 5xx | 均有指数退避重试；连续失败会跳过并记入 trace 的 `warn` 事件，不中断研究 |

---

*关联文档：README.md（业务背景）→ PRD.md（需求）→ TECH_DESIGN.md（技术方案）→ TASKS.md（任务拆解）→ 本文（测试）。*
