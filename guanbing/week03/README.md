# LangChain 入门教程（3 个核心示例）

本目录包含 LangChain 从「模型调用 → 工具调用 → 多智能体」的 3 个循序渐进的入门示例。

---

## 📦 环境依赖

```bash
pip install langchain langchain-openai langchain-core python-dotenv
```

（如果项目有虚拟环境，先 `source .venv/bin/activate` 再装。）

---

## 🔑 API Key 的调用方式：通过 `.env` 文件（安全无硬编码）

3 个脚本的 API Key **均不写在代码里**，而是从**上一级目录**的 `.env` 文件加载：

```
Week03-课程代码/
├── .env                      ← 在这里配置 API Key（不要上传到公开仓库！）
└── 01_langchain教程/          ← 你当前所在的目录
    ├── 01_callModels_GB_DXB.py
    ├── 02_modelCallTools_GB_DXB.py
    └── 03_subagent_GB_DXB.py
```

### `.env` 配置示例

支持 **DeepSeek** 和 **Qwen** 两个供应商二选一，通过 `LLM_PROVIDER` 切换：

```env
# ===== 选择默认模型：deepseek 或 qwen =====
LLM_PROVIDER=deepseek

# ===== DeepSeek =====
DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxx
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEEPSEEK_MODEL=deepseek-v4-flash

# ===== Qwen（阿里云 DashScope，如需使用请取消注释并填 Key）=====
# QWEN_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxx
# QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
# QWEN_MODEL=qwen-plus
```

### 代码中如何读取（原理）

每个脚本开头都会执行：

```python
dotenv_path = Path(__file__).parent.parent / ".env"   # 找到上级目录的 .env
load_dotenv(dotenv_path=dotenv_path)                  # 注入环境变量
os.getenv("DEEPSEEK_API_KEY")                          # 读取 Key（深拷贝到内存，不落盘）
```

> 所以「重命名本目录的 Python 文件名」不会影响 `.env` 读取；但**不要把 Python 文件移出 `01_langchain教程/` 目录**，否则 `parent.parent` 会定位错误。

---

## 📚 3 个文件功能说明 & 运行方式

### 1️⃣ `01_callModels_GB_DXB.py` — 基础模型调用

**功能**：演示 3 种和大模型对话的方式
- 直接传字符串：`llm.invoke("你好")`
- 手动构造消息对象：`HumanMessage(...)` 列表
- 构建多轮对话历史 + `pretty_print()` 美化输出

**运行**：
```bash
python 01_callModels_GB_DXB.py
```

---

### 2️⃣ `02_modelCallTools_GB_DXB.py` — 工具调用（Tool Calling）

**功能**：演示「Tool Calling 三段式流程」：
1. 模型**决策**要调用哪个工具、传什么参数
2. 本地**执行**工具函数（内置天气查询工具）
3. 把工具结果丢回模型**汇总**成自然语言回答

> 内置工具：`get_weather(city)`（北京/上海/武汉 有模拟数据，其他城市默认为晴天）

**运行**：
```bash
python 02_modelCallTools_GB_DXB.py
```

---

### 3️⃣ `03_subagent_GB_DXB.py` — 多智能体（Master + Sub Agent）

**功能**：演示 Multi-Agent 架构
- **Weather Sub-Agent**：专职「天气信息格式化整理」，带独立 system prompt
- **Master Agent**：充当总经理，判断用户问题 → 调用 `get_weather_agent` 工具 → 把结果整理给用户

> 关键设计：子 Agent 通过 `@tool` 装饰器包装成普通工具，Master 根本不知道工具内部还嵌了一个 LLM。

**运行**：
```bash
python 03_subagent_GB_DXB.py
```

---

## ⚠️ 注意事项

1. **不要把 `.env` 上传到 Git**（建议加进 `.gitignore`）
2. 3 个脚本均为**独立入口**，互不依赖、互不 import
3. 想切换模型供应商？只需改 `.env` 的 `LLM_PROVIDER`，代码无需动
