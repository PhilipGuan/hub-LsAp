# padow-ai: Week2 人物关系抽取作业（JSON Mode）

本仓库用于提交 Week2 课程作业：基于 LLM JSON Mode 实现的人物关系抽取智能体。

- 方案：纯 JSON Mode（非 function/tool call）
- 输出格式：A1（顶层直接是数组）
- 关系命名：R2（英文标签，如 `admires`, `appreciates`, `friend_of`, `disagrees_with`）
- 错误处理：E1（不中断，失败返回空数组或兜底语料）
- 语料生成：C（通用人物关系语料，默认 5 条）

## 1. 运行的最小前提

- Python 3.10+（本地建议 3.12）
- 一个可调用的 OpenAI 兼容 API key（本作业默认使用 DeepSeek，Qwen 配置也已支持）
- 网络能访问对应 `base_url`

## 2. 安装依赖

建议使用虚拟环境：

```bash
cd <本项目根目录>
python -m venv .venv
source .venv/bin/activate         # macOS / Linux
pip install -r requirements.txt
```

> 说明：`requirements.txt` 是全项目依赖。
> 若只想最小安装 Week2 作业所需，至少保证已安装：
> `python-dotenv`、`openai`、`pydantic`。

## 3. 配置你自己的 API Key（不会被提交）

本项目通过 `.env` 读取密钥，**严禁把真实 key 写进任何被提交的 py / md / notebook 文件**。

步骤：

```bash
# 1) 在项目根目录，复制模板为真实 .env
cp .env.example .env

# 或者如果你想把 .env 放在 Week2/ 下（也支持，会被优先读取）：
cp .env.example Week2/.env
```

然后编辑 `.env`（或 `Week2/.env`），至少填好：

```ini
DEEPSEEK_API_KEY=你的真实_DeepSeek_API_KEY
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEEPSEEK_MODEL=deepseek-v4-flash
```

如果之后想切到 Qwen，也可以填：

```ini
QWEN_API_KEY=你的真实_Qwen_API_KEY
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_MODEL=qwen-plus
```

运行时默认 provider 为 `deepseek`，可用环境变量覆盖：

```bash
LLM_PROVIDER=qwen ./.venv/bin/python ./Week2/run_relation_homework.py
```

## 4. 目录说明（本次作业实际会用到的文件）

```
.
├── Week2/
│   ├── api_llm.py                # 多 provider LLM 封装：.env 加载、json_mode、重试
│   ├── relation_extractor.py     # 语料生成 + 关系抽取 + 报告打印（核心）
│   └── run_relation_homework.py  # 作业入口：生成 5 条语料 + 跑示例句 + 打印报告
├── .env.example                  # key 模板（请复制为 .env 后再填）
├── .gitignore                    # 已忽略 .env / .venv / __pycache__ / notebook 缓存
└── requirements.txt              # 全项目依赖（Week2 作业也可直接用）
```

## 5. 如何运行作业

### 5.1 一键跑完整作业（最推荐）

```bash
cd <本项目根目录>
./.venv/bin/python ./Week2/run_relation_homework.py
```

运行后你会看到 4 段输出：

1. `.env` 加载位置
2. 生成的 5 条人物关系语料
3. 作业题目里的示例句：
   - 输入：`小明喜欢小姚，但是小姚喜欢小王。`
   - 预期 A1 输出（顶层数组）：

     ```json
     [
       {"source": "小明", "relation": "admires", "target": "小姚"}
     ]
     ```

4. 对 5 条新语料批量抽取后的最终报告（每条都会显示 `OK: True/False`）

### 5.2 只抽取一条自定义文本

进入 Python / Notebook 后直接调用：

```python
from Week2.relation_extractor import extract_relation_graph

text = "小明喜欢小姚，但是小姚喜欢小王。"
graph = extract_relation_graph(text, provider="deepseek")
print(graph)
# 期望输出长度 >=0 的 list，元素字段固定为 source / relation / target
```

## 6. 提交到 GitHub 的安全规则（必读）

**以下文件绝对不能 `git add`：**

- `.env`
- `Week2/.env`
- `.venv/`
- `__pycache__/`
- `*.pyc`
- `.ipynb_checkpoints/`

它们已被根目录 `.gitignore` 自动忽略，只要你不手动强制 add，就不会提交。

### 6.1 建议提交的最小文件集合

如果老师要的是“Week2 这一次作业可复现”，建议提交：

```
.
├── Week2/
│   ├── api_llm.py
│   ├── relation_extractor.py
│   └── run_relation_homework.py
├── .env.example
├── .gitignore
├── README.md
└── requirements.txt
```

如果你只提交 `Week2/` 下的 py 文件，**必须至少包含 3 个 py 文件**，而不是 2 个：
- 缺 `api_llm.py` 会导致 `from api_llm import chat, load_env` 直接 `ImportError`，别人跑不起来。

## 7. 预期的输出规范（作业评分用）

每条抽取结果都必须是数组，数组元素结构固定为：

```json
{
  "source":   "<人物姓名>",
  "relation": "<英文关系标签，小写+下划线，例如 admires / friend_of / disagrees_with>",
  "target":   "<人物姓名>"
}
```

- 当句子中没有可抽取的人物关系时，按 E1 返回 `[]`（空数组），程序不应崩溃。
- 当 LLM 返回非标准 JSON（被 markdown 包裹、截断、字段缺失等）时，`relation_extractor.py` 会尝试清洗与兜底，最终仍保证顶层是数组 A1。

## 8. 常见问题

**Q1：运行提示 “未在环境变量中找到 DEEPSEEK_API_KEY”**
A：说明 `.env` 没加载或没填对。确认 `.env` 放在项目根或 `Week2/` 下，且 key 字段名写成 `DEEPSEEK_API_KEY`（不要加外层引号）。

**Q2：我想临时改模型，不想改 .env？**
A：用环境变量一次性覆盖：

```bash
RELATION_MODEL=deepseek-chat ./.venv/bin/python ./Week2/run_relation_homework.py
```

**Q3：模型返回的 `relation` 不是英文怎么办？**
A：系统 prompt 已强约束为英文标签，默认概率很高。若模型仍返回中文会在日志里打印 warning，并保留原始字符串（E1 不中断）。你可以在 [relation_extractor.py](Week2/relation_extractor.py) 的 `_normalize_graph` 中按需追加映射表或过滤逻辑。
