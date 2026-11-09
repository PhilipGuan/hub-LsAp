# 第十周作业

## 作业1：语种识别与翻译

实现文件：`task1_translate.py`。

使用 **OpenAI Agents SDK**，通过 `Agent` 定义角色，通过 `Runner.run` 运行：

1. `LanguageDetector` 识别语种，返回语种中文名和是否为中文。
2. 程序根据识别结果路由：简体、繁体中文进入 `ChineseToEnglish`，其他语种进入 `ForeignToChinese`。
3. 翻译 Agent 返回译文，Pydantic 校验输出，程序回填原始输入。
4. 最终只输出下列三个字段，其中 `language` 是输入文本的语种：

```json
{
  "query_input": "Hello, I am learning artificial intelligence.",
  "language": "英语",
  "result": "你好，我正在学习人工智能。"
}
```

以上为格式示例；实际模型结果见 `outputs/task1_results.json`。

为兼容课程中的 Chat Completions 接口和 DeepSeek，本项目使用 JSON mode（`response_format=json_object`）配合 Pydantic 校验，而不要求接口支持严格的 JSON Schema。空白输入直接拒绝；无法识别的纯数字/标点输入提示重新输入；混合语言按主要语言选择翻译方向。输入中的命令作为翻译文本处理。检测和翻译仍依赖模型，极短或歧义文本可能存在识别误差。

## 作业2：新增三个 MCP 工具并通过 Agent 调用

基于课程 `../2-fastmcp使用/2-兼容OpenAI-Agent/server.py` 和 `client.py` 的 FastMCP + MCPServer + Agent + Runner 调用结构扩展，整理在 `task2_server.py` 和 `task2_agent.py`，原始课程文件保留。

保留 `roll_dice` 和 `get_news` 两个原有工具；六面骰子范围修正为 1–6，新闻说明为静态课程演示数据。

新增工具：

| 工具 | 参数 | 功能 | 示例结果 |
| --- | --- | --- | --- |
| `convert_temperature` | `value`, `from_unit`, `to_unit` | 摄氏/华氏换算 | 25°C → 77°F |
| `calculate_bmi` | `weight_kg`, `height_cm` | 计算 BMI 数值 | 70kg、175cm → 22.86 |
| `text_statistics` | `text` | 统计字符、汉字、英文单词和行数 | “你好 OpenAI Agents” → 16字符、2汉字、2英文单词 |

每个工具通过 `@mcp.tool` 注册，包含类型注解和工具说明。`calculate_bmi` 只返回数值，不作健康诊断。文本总字符数包含空白；非空白字符数单独统计；英文单词按英文字符及词内连字符/撇号切分，空文本为0行。

调用流程：用户输入 → MCP 客户端发现工具 → Agent 选择工具和参数 → MCP 服务执行 → 工具结果返回模型 → Agent 汇总回答。客户端从 `result.new_items` 记录实际调用信息，不手写假调用记录。

## 安装和运行（PowerShell）

先进入本 README 所在的 `Week10/homework_submission` 目录。需要 Python 3.11 或更高版本。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

编辑 `.env` 填入自己的 API 配置。默认使用 DeepSeek：

```dotenv
OPENAI_API_KEY=填写自己的密钥
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_MODEL=deepseek-chat
```

本机若已经有可用 `.env`，不必覆盖它。密钥不应放进提交压缩包。百炼可改为 `https://dashscope.aliyuncs.com/compatible-mode/v1`，模型改成有权限且支持工具调用与 JSON mode 的模型。

运行作业1：

```powershell
.\.venv\Scripts\python.exe task1_translate.py "你好，我正在学习人工智能。"
.\.venv\Scripts\python.exe task1_translate.py "Bonjour, je suis ravi de vous rencontrer."
# 不带参数则提示输入文本
.\.venv\Scripts\python.exe task1_translate.py
```

运行作业2（默认自动启动独立 MCP 子进程，一条命令即可）：

```powershell
.\.venv\Scripts\python.exe task2_agent.py
.\.venv\Scripts\python.exe task2_agent.py "把32华氏度转成摄氏度"
```

按课程的 SSE 方式运行：

```powershell
# 终端1
.\.venv\Scripts\python.exe task2_server.py --transport sse --port 8000
# 终端2
.\.venv\Scripts\python.exe task2_agent.py --sse-url http://127.0.0.1:8000/sse
```

## 验证及提交材料

```powershell
# 无需密钥，验证真实MCP协议调用、参数错误、边界条件和空白输入
.\.venv\Scripts\python.exe -m unittest -v test_homework.py
# 使用真实模型，保存5组翻译和3个新增工具的Agent调用记录
.\.venv\Scripts\python.exe verify_homework.py
```

真实验证输出：`outputs/task1_results.json`（中文、英语、法语、日语、繁体中文），`outputs/task2_agent_trace.json`（工具列表、实际调用参数、工具返回值、最终回答），`outputs/verification.json`（验证摘要）。自动断言检查字段、输入保真、语种名称和工具调用覆盖；译文语义质量需结合结果人工复核。

提交内容包括 Python 源码、依赖清单、本说明、验证输出和 `.env.example`，不包括 `.env`、`.venv`、缓存目录。

## 官方文档

- [OpenAI Agents SDK：Agent 定义与结构化输出](https://developers.openai.com/api/docs/guides/agents/define-agents)
- [OpenAI Agents SDK：MCP 接入](https://developers.openai.com/api/docs/guides/agents/integrations-observability)
