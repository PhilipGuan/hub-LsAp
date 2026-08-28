# 第二周作业：人物关系分析智能体

本目录包含第二周作业的可运行代码和提交说明。

## 作业内容

### 作业 1：Streamlit 环境与课程页面

课程提供的四个页面位于：

```text
../02-课程资料/03Week02_2026Q2/src/pages/
├── 01_倒排索引.py
├── 02_编码模型.py
├── 03_机器学习.py
└── 04_FAQ检索.py
```

从课程项目根目录启动：

```powershell
conda activate base
cd "C:\Users\haozh\Desktop\cours\AI_cours\week2\02-课程资料\03Week02_2026Q2"
python -m streamlit run src\streamlit_app.py
```

建议提交首页和四个页面的运行截图，证明本地环境、模型与页面可以正常使用。

### 作业 2：人物关系抽取智能体

本项目使用大模型的 JSON Mode 或 Tool Calling，从自然语言中抽取全部人物关系，并展示结构化 JSON 和有向关系图谱。

输入：

```text
小明喜欢小姚，但是小姚喜欢小王。
```

输出见 `sample_output.json`。原句包含两条明确关系，因此程序会完整抽取两条，而不是只返回第一条。

## 文件结构

```text
homework/
├── app.py                       # Streamlit 作业界面
├── relationship_agent.py        # JSON Mode / Tool Calling 核心代码
├── sample_output.json           # 示例结构化结果
├── requirements.txt             # 独立运行所需依赖
├── .env.example                 # 安全的配置模板
├── tests/
│   └── test_relationship_agent.py
└── screenshots/
    └── README.md                # 截图清单
```

## 安装

当前电脑的 Anaconda `base` 环境已经安装主要依赖。如需在新环境中复现：

```powershell
conda activate base
cd "C:\Users\haozh\Desktop\cours\AI_cours\week2\homework"
python -m pip install -r requirements.txt
```

## 配置大模型

复制配置模板：

```powershell
Copy-Item .env.example .env
```

编辑 `.env`：

```dotenv
LLM_API_KEY=填写自己的API密钥
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
```

也可以不创建 `.env`，直接在 Streamlit 左侧栏填写。项目使用 OpenAI 兼容接口，因此可更换为其他兼容服务的 Base URL 和模型名。

> `.env` 已加入 `.gitignore`。提交作业前不要把真实 API Key 写入代码、README 或截图。

## 运行

```powershell
conda activate base
cd "C:\Users\haozh\Desktop\cours\AI_cours\week2\homework"
python -m streamlit run app.py
```

浏览器打开终端显示的 `http://localhost:8501`。在页面中：

1. 填写 API Key、Base URL 和模型名称。
2. 选择 `JSON Mode` 或 `Tool Calling`。
3. 输入人物关系描述。
4. 点击“开始分析”。
5. 查看 JSON、关系数量和人物关系图谱。

“查看示例结果”不会调用 API，仅用于预览界面；正式作业演示应使用“开始分析”。

## 测试

单元测试使用模拟的大模型响应，不消耗 API Token：

```powershell
python -m pytest -q
```

命令行实际调用（需要 `.env`）：

```powershell
python relationship_agent.py
```

## 提交检查

- [ ] 课程 Streamlit 首页截图
- [ ] 倒排索引页面截图
- [ ] 编码模型页面截图
- [ ] 机器学习页面截图
- [ ] FAQ 检索页面截图
- [ ] 人物关系智能体输入、JSON 与图谱截图
- [x] 人物关系智能体实现代码
- [x] 依赖清单与运行说明
- [x] API Key 未写入提交文件

建议提交整个 `homework` 目录，并将上述截图放入 `screenshots`。
