# 深度研究助手（后端）

输入一个研究主题，自动执行 agentic 研究循环（规划 → 多轮检索 → 阅读抽取 → 判断补检 → 综合），产出**带来源引用的研究报告**四类产物：

1. **结构化报告**（摘要、分节正文、关键结论、遗留问题）
2. **来源列表**（每条结论可追溯到 URL / 标题 / 来源）
3. **研究过程记录**（检索了哪些关键词、读了哪些页、迭代了几轮）
4. **置信度说明**（等级、信息截止时间、无来源结论标注「模型推断」）

## 安装

```bash
cd backend
pip install -r requirements.txt
```

## 配置

需要两个 API key（均可写入 `backend/.env` 或设为环境变量）：

| 变量 | 说明 |
|------|------|
| `DEEPSEEK_API_KEY` | DeepSeek LLM（必填，否则无法规划/抽取/综合） |
| `BOCHA_API_KEY` | Bocha 搜索（已有默认值，见 README 根文档） |

`.env` 示例：

```
DEEPSEEK_API_KEY=sk-xxx
```

## 使用

### CLI（一次性研究）

```bash
python -m backend "2026 年新能源汽车行业趋势"
```

产出落盘到 `output/<slug>/`，内含：

- `report.json` —— 唯一数据源（四类产物合一）
- `report.md` —— 人读 Markdown
- `report.html` —— 浏览器双击即看（含来源引用）

### HTTP 层（发起 + 轮询）

```bash
python -m backend.web.server
# 或 uvicorn backend.web.server:app --reload
```

- `POST /research`  body `{"topic": "..."}` → 返回 `{"id": "...", "status": "running"}`
- `GET /research/<id>` → 查询状态与结果（`running` / `done` / `error`）
- `GET /research` → 列出所有任务
- `/output/<slug>/report.html` → 静态浏览已产出的报告

## 架构与数据流

```
app（CLI / HTTP）→ research（编排）→ engine（agentic 循环）
                                  → agent（DeepSeek LLM + 提示词）
                                  → tools（Bocha 搜索）
                          → storage（落盘 json/md/html）
```

| 模块 | 职责 |
|------|------|
| `models.py` | pydantic 模型集中 + slug 生成 |
| `config.py` | 环境变量 / 参数读取 |
| `app/` | CLI 入口 |
| `web/` | FastAPI HTTP 层 |
| `research/` | 编排：串起 engine + storage |
| `engine/` | agentic 研究循环（规划/检索/抽取/补检/综合） |
| `agent/` | `llm.py`（DeepSeek 封装）+ `prompts.py`（提示词，与代码分离） |
| `tools/` | Bocha 搜索 |
| `storage/` | 落盘 json / md / html |

## 各模块 demo

每个模块带 `__main__` 自检（需 key 的已标注）：

```bash
python -m backend.tools      # 搜一次
python -m backend.storage    # 样例落盘（无需 key）
python -m backend.agent      # 调一次 LLM 规划（需 key）
python -m backend.engine "主题"   # 完整研究（需 key）
python -m backend.research "主题" # 研究 + 落盘（需 key）
```

## 研究循环参数

在 `config.py`（或环境变量）调整：

- `MAX_ROUNDS` —— 总检索轮次上限（默认 3：首轮 + 最多 2 次补检）
- `MIN/MAX_SUB_QUESTIONS` —— 子问题拆分数（默认 3~5）
