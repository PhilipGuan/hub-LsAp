# LangChain 零基础入门（PM 版）

> 目标：读完这份文档，你能（1）**用自己的话**说清 LangChain 是什么、解决什么问题；
> （2）**对着本项目的代码**指出每个概念在哪；（3）把同一套思路**迁移**到别的 AI 产品上；
> （4）在 **AI PM 面试**里答得上主流问题。
>
> 阅读方式：不必一次看完。**第一部分搞懂心智模型**；**第二部分先看「概念全景」总表，再看逐个展开**；
> 第三部分举一反三；第四部分留给面试前刷。

---

## 第一部分：心智模型（先记三句话）

### 第 1 句：LangChain 不是模型，是「模型之外的工程」

大模型本身只会做一件事：**给一段文本，回一段文本**。它不会记你上次聊了什么、
不会查你的数据库、不会按固定流程一步步干活。

> **LLM = 一个很聪明但只有嘴没有手脚的大脑。**
> LangChain 做的，是给这个大脑配上**手脚（工具）、记忆（历史）、流程（编排）和说明书（提示词模板）**。

所以 LangChain 属于**应用层框架**，它**不训练、不提供模型**。你用的是 DeepSeek / GPT / Qwen 的模型，
LangChain 只负责「怎么把它用好」。

### 第 2 句：它把「散落的胶水代码」标准化

不用框架也能做：直接调 OpenAI 的 SDK，自己拼字符串、自己写重试、自己 `json.loads`。
这就是本项目**重写前**的样子（`04_SQL-Code-Agent-Demo`）。

问题在哪？胶水代码会越长越乱，而且每换一个模型/供应商就要重写一遍。
LangChain 把这些重复劳动**标准化成一套通用部件**：

| 你要做的事 | 自己写（散落、易错） | LangChain 的标准部件 |
|---|---|---|
| 调模型 | `OpenAI(...).chat.completions.create(...)` | `ChatOpenAI(...)` |
| 拼提示词 | f-string 拼字符串 | `ChatPromptTemplate` |
| 要结构化 JSON | 提示词求模型输出 JSON + `json.loads` + 兜底 | `with_structured_output(Pydantic)` |
| 失败重试 | `for i in range(3): try/except` | `model.with_retry(...)` |
| 让模型调函数 | 手写一层判断解析 | `@tool` + `bind_tools` |
| 串流程 | `if/else` 手写 | `StateGraph`（LangGraph） |

### 第 3 句：LangChain v1 里，「编排」交给了 LangGraph

LangChain v1（当前版本）把**复杂的流程编排**这一块，交给了它的兄弟库 **LangGraph**。
`create_agent`（LangChain v1 里创建 Agent 的标准方式）**内部就是用 LangGraph 实现的**。

> 打个比方：
> - **LangChain** = 一箱标准零件（模型接口、提示词模板、工具、输出解析）。
> - **LangGraph** = 把这些零件**按一张流程图**装起来的那张图纸（节点、连线、条件分支、循环）。
>
> 面试里常见的一个错误认知是「LangChain 和 LangGraph 二选一」——**不是**，一个管「每个零件是什么」，一个管「零件之间怎么连」。

---

## 第二部分：核心概念全景 × 本项目代码

### 先看全景：官方心智模型与概念地图

> 对齐官方文档：[LangChain overview](https://docs.langchain.com/oss/python/langchain/overview)、
> [Agents](https://docs.langchain.com/oss/python/langchain/agents)、
> [Middleware](https://docs.langchain.com/oss/python/langchain/middleware/overview)。
> **本节只立框架**；细节在接下来逐个展开，或后续按需追加。

#### ① 官方的一句话心智模型：`Agent = Model + Harness`

官方定义：**Agent 就是「模型在循环里反复调用工具，直到任务完成」**；而 **Harness（外挂装置）
就是那个循环之外的一切** —— 提示词、工具，以及塑造模型行为的**中间件**。

> 呼应第一部分：**Model = 大脑；Harness = 手脚 + 记忆 + 流程 + 护栏。**
> LangChain 交付的 `create_agent` 本体就是一个「**高度可配置的 harness**」：
> 最小骨架只要 `model` + `tools`，其余能力全靠**中间件**按需加装。

#### ② 官方给的 4 个核心价值（Core benefits）

| 官方价值 | 一句话 | 本项目在哪 |
|---|---|---|
| **Standard model interface** 标准模型接口 | 一套接口通吃各家模型 / 嵌入 | `ChatOpenAI` 同一份代码接 DeepSeek & Qwen（概念 1） |
| **Highly configurable harness** 可配置外挂 | `create_agent` 起步 + 中间件加装 | 路线 B：[agents/sql_agent.py](sqlagent/agents/sql_agent.py) |
| **Built on top of LangGraph** 底层是 LangGraph | 白拿持久化 / 人工介入 / 断点续跑 | 路线 A：[workflow/graph.py](sqlagent/workflow/graph.py) |
| **Debug with LangSmith** 可观测 | trace / 工具调用 / 状态流转一处看全 | ➖ **未用**（本项目用 notebook 打印 trace 代替） |

#### ③ 生态定位：四个名字别搞混（官方明确区分）

| 名字 | 是什么 | 什么时候用它 | 本项目 |
|---|---|---|---|
| **LangChain** | 高阶 harness（`create_agent`） | 快速搭一个可定制的 Agent | ✅ 路线 B |
| **LangGraph** | 低阶编排框架 | 确定性流程与 Agent 混合、要精细控制 | ✅ 路线 A |
| **Deep Agents** | 已「装好电池」的 harness（规划 / 文件系统 / 子 Agent / 记忆） | 不想自管基础设施，直接上生产 | ➖ 未用 |
| **LangSmith** | 可观测 / 评测 / 调试平台 | trace、调试、评测 | ➖ 未用 |

#### ④ 概念地图（本项目视角：三层 + 状态标记）

标记：✅ = 本文已展开；🔍 = 已列框架、**待后续追加细节**；➖ = 本项目未用（仅备注）。

| 层 | 概念 | 官方文档 | 状态 |
|---|---|---|---|
| **① 接入层**<br>怎么跟模型说话 | Chat Model 模型接入 | models | ✅ 概念 1 |
| | Messages 消息 | messages | ✅ 概念 2 |
| | Prompt Template 提示词模板 | prompts | ✅ 概念 3 |
| | Structured Output 结构化输出 | structured-output | ✅ 概念 4 |
| | Embeddings & Retrieval 嵌入与检索 | retrieval | 🔍 概念 14 |
| **② 构建块**<br>怎么拼出一个 Agent | Tools 工具 | tools | ✅ 概念 5 |
| | Agent 智能体 | agents | ✅ 概念 6 |
| | **Middleware 中间件** | middleware | 🔍 概念 7 |
| | **Agent State & Memory 状态与记忆** | short-term-memory | 🔍 概念 8 |
| **③ 运行时与工程**<br>怎么让它稳在生产 | **Streaming 流式** | streaming | 🔍 概念 9 |
| | **Context Management 上下文管理** | context-engineering | 🔍 概念 10 |
| | **Fault Tolerance 容错** | middleware/built-in | 🔍 概念 11 |
| | **Guardrails & HITL 护栏与人工介入** | middleware/built-in | 🔍 概念 12 |
| | **Observability 可观测性** | langsmith | 🔍 概念 13 |
| | Runnable / LCEL 组合协议 | lcel | ✅ 文末「附」 |

> **读法**：① 是「怎么跟模型说话」，② 是「怎么拼出一个 Agent」，③ 是「怎么让它稳定跑在生产上」。
> 先立起这三层，才不会把「一个函数」「一个框架」「一个平台」混为一谈。

---

下面每个概念都给你：**一句话定义 + 本项目它在哪 + 为什么要它**。
（**概念 1–6 逐个展开**；**概念 7–14 是官方概念地图的其余部分**，本次先立框架、给对照，细节后续按需追加。）

### 概念 1：Chat Model（模型接入层）—— 「换模型只改一处」

- **是什么**：一个统一接口，不管你背后是 OpenAI、DeepSeek 还是通义，代码都长得一样。
- **本项目在哪**：[sqlagent/llm.py](sqlagent/llm.py) 的 `build_raw_model()`。

```python
ChatOpenAI(model=..., base_url=..., api_key=..., temperature=0.0)
```

- **为什么重要（PM 视角）**：
  - **成本**：产品上线后想从 GPT 换成更便宜的国产模型？改一行。
  - **合规**：某些行业必须用私有化部署的模型？改一行。
  - **不被锁定**：这是「技术选型不被单一供应商绑架」的关键。

> 一个小细节值得注意：本项目**没有**把 API Key 写进全局环境变量（重写前的实现要「伪装」成
> `OPENAI_API_KEY` 才行）。`ChatOpenAI` 支持直接传参——**更干净，也更安全**。

#### 深入一层：为什么叫 `ChatOpenAI`？它能兼容 DeepSeek / Qwen 吗？

**能——而且本项目默认跑的就是 DeepSeek（`deepseek-v4-flash`）。**

关键点：`ChatOpenAI` 里的 "OpenAI" **指的不是「OpenAI 这家公司」，而是「OpenAI 兼容协议」**。
OpenAI 的 Chat Completions 接口早已成为行业事实标准，DeepSeek、通义千问（DashScope 兼容模式）、
Moonshot、智谱、vLLM、Ollama…… 都提供了「长得和 OpenAI 一模一样」的端点。

> **类比**：`ChatOpenAI` 不是「只配 OpenAI 门锁的钥匙」，而是
> **「按 OpenAI 那把锁的规格打造的钥匙」**。只要对方的门是同规格，换个地址、换把钥匙就能开。

**真正实现「换一家模型」的只有 3 个参数**：

| 参数 | 它决定什么 | 本项目（DeepSeek）取值 |
|---|---|---|
| `base_url` | **请求发到哪**（换供应商） | `https://api.deepseek.com/v1` |
| `api_key` | **用谁的密钥**（换账号） | `$DEEPSEEK_API_KEY` |
| `model` | **调哪一档模型** | `deepseek-v4-flash` |

其余全部沿用 OpenAI 规范——消息格式（`messages`）、返回结构（`choices[0].message.content`）、
流式、**工具调用**、**JSON 模式**——所以一行代码都不用改。

在 [sqlagent/config.py](sqlagent/config.py) 里能直接看到这一点：`_PROVIDERS` 表把 `deepseek` 和 `qwen`
并排列出，二者**只是 `base_url` / 环境变量名不同**，走的却是同一个 `ChatOpenAI`：

```python
_PROVIDERS = {
    "deepseek": ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL",
                 "https://api.deepseek.com/v1", "deepseek-v4-flash"),
    "qwen":     ("QWEN_API_KEY", "QWEN_BASE_URL", "QWEN_MODEL",
                 "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
}
```

> 注意 Qwen 那行的 URL 里有个 `compatible-mode`——**「兼容模式」四个字就是全部秘密**：
> 阿里专门开了一个「按 OpenAI 规格说话」的入口，所以能直接被 `ChatOpenAI` 接走。

**那如果对方「不是」OpenAI 兼容协议呢？** 那才需要换类：

| 供应商 / 场景 | 该用的类 | 依赖包（本项目**均未安装**，仅作了解） |
|---|---|---|
| 一切 OpenAI 兼容端点（OpenAI / DeepSeek / Qwen 兼容模式 / Moonshot / GLM / vLLM …） | **`ChatOpenAI`** | `langchain-openai` ✅ 已装 |
| Anthropic Claude（原生协议） | `ChatAnthropic` | `langchain-anthropic` |
| Google Gemini（原生协议） | `ChatGoogleGenerativeAI` | `langchain-google-genai` |
| 通义千问（DashScope 原生 SDK） | `ChatTongyi` | `langchain-community` + `dashscope` |
| 本地 Ollama | `ChatOllama` | `langchain-ollama` |
| DeepSeek 官方封装 | `ChatDeepSeek` | `langchain-deepseek` |

本项目**刻意只用 `ChatOpenAI` + 兼容端点**：依赖最少、切换最省事。
（LangChain 还有个统一工厂 `init_chat_model()`，能按供应商 / 模型名直接返回对应模型类，思路一样，
理解即可——本项目没用它，因为显式写 `ChatOpenAI` 更直白、可控。）

**「兼容」的真实边界（PM 必须知道的坑）**：兼容的是**基础接口**（发消息、收消息）；
**高级能力各家支持度并不一致**——比如某些兼容端点对 tool calling、JSON 模式的支持就不完整。
本项目为此留了个后门：用环境变量 `STRUCTURED_OUTPUT_METHOD` 在 `json_mode`（`response_format`）
和 `function_calling`（工具调用）两条路径间切换（见 [sqlagent/llm.py](sqlagent/llm.py#L29-L33)）——
**这就是「换模型只改一处」在真实世界里的样子：主路径不动，只留一个可控的开关。**

**面试一句话**：
> `ChatOpenAI` 接的是「OpenAI **兼容协议**」，不是「OpenAI 这家公司」。DeepSeek、Qwen 都提供兼容端点，
> 所以换 `base_url` / `api_key` / `model` 三个参数即可。它体现的是「**协议标准化 → 供应商可替换**」；
> 但要注意兼容的只是**基础接口**，高级能力（工具调用、结构化输出）各家仍有差异，必须在测试里覆盖。

### 概念 2：Messages（消息）—— 「对话是有角色的」

- **是什么**：对话不是一坨字符串，而是有角色标签的消息列表。
  - `SystemMessage`：给模型的**人设/规则**（「你是 SQLite 专家」）。
  - `HumanMessage`：用户说的话。
  - `AIMessage`：模型回的话（还可能带「我要调用某个工具」的意图）。
  - `ToolMessage`：工具执行完的结果（喂回给模型）。
- **本项目在哪**：提示词定义在 [sqlagent/prompts.py](sqlagent/prompts.py)，
  实际调用在 [llm.py](sqlagent/llm.py) 的 `invoke_text()`。

- **为什么重要**：把「人设」和「用户输入」分开，是提示工程的基本功。
  模型对 `SystemMessage` 的服从度通常高于混在正文里的同类句子。

### 概念 3：Prompt Template（提示词模板）—— 「提示词当代码管」

- **是什么**：把提示词里的**变量**抽出来，做成可复用的模板。

```python
SELECT_TABLES_PROMPT.format_messages(catalog=..., question=...)
```

- **本项目在哪**：[sqlagent/prompts.py](sqlagent/prompts.py)。注意：**提示词的正文文字逐字保留了原文**，
  只把「拼字符串」换成了「模板格式化」——这是刻意的，**换框架不等于换行为**。

- **为什么重要（PM 视角）**：提示词就是 AI 产品的**核心资产**，应该像代码一样
  被版本管理、被 review、被 A/B 测试，而不是散落在 notebook 里。

### 概念 4：Structured Output（结构化输出）—— 「别让模型的自由发挥毁了下游」

- **是什么**：让模型直接吐出**符合你定义的结构**，而不是一段可能格式错乱的文本。

```python
class ScopeJudgement(BaseModel):
    scope: Literal["in", "out", "ambiguous"]
    reason: str
    tables: list[str]
    clarify: str
```

- **本项目在哪**：契约定义在 [sqlagent/schemas.py](sqlagent/schemas.py)，
  调用在 [llm.py](sqlagent/llm.py) 的 `invoke_structured_messages()`。

- **为什么重要**：这是「能不能上生产」的分水岭。
  靠提示词说「请输出 JSON」+ `json.loads`，模型偶尔会：多说一句话、少个字段、多个逗号——**线上就会报错**。
  结构化输出把「**靠约定**」变成「**靠校验**」，解析失败可显式降级，而不是悄悄出错。

> 本项目甚至保留了原文的「解析失败要兜底、不猜」的语义：`invoke_structured_messages()`
> 失败时返回 `None`，让上层走降级分支（本项目里是「降级为反问澄清」）。

### 概念 5：Tools（工具）—— 「给大脑装手脚」

- **是什么**：把普通 Python 函数**暴露给模型调用**。用 `@tool` 装饰器，工具的名字、
  参数、说明会自动从**函数签名 + docstring**生成，模型据此决定「什么时候该用哪个工具」。

- **本项目在哪**：[sqlagent/tools.py](sqlagent/tools.py) 的 `build_tools()`，三个工具：
  - `list_tables()`：列出所有表。
  - `parse_table(table_name)`：看某张表的字段。
  - `run_sql(sql)`：执行一条**只读** SQL。

- **为什么重要（PM 视角）**：**工具就是 AI 产品的「能力边界」**。
  你的产品能干什么、不能干什么，在产品定义阶段，本质就是在定义「提供哪些工具」。
  本项目最值得注意的一点是：`run_sql` 工具**内部包了只读守卫**——
  **安全不能寄希望于模型「自觉遵守提示词」，要做进工具本身**。

### 概念 6：Agent（智能体）—— 「让模型自己做决定」

- **是什么**：一个「模型 + 工具箱 + 循环」：模型判断该调哪个工具 → 拿到结果 → 再判断 → ……直到给出答案。
- **本项目在哪**：两条路都演示了，这是理解 Agent 的最佳素材：

| | 路线 A：`workflow/graph.py` | 路线 B：`agents/sql_agent.py` |
|---|---|---|
| 谁决定下一步 | **写在 Python 里**（固定 7 步） | **模型自己决定**（调哪个工具、调几次） |
| LangChain 部件 | LangGraph `StateGraph` | `create_agent` |
| 特点 | 可预测、可审计、可单测 | 灵活、开放、行为更不可控 |
| PM 类比 | 「流水线工厂」 | 「请了个顾问，你只给目标和工具」 |

> **这是 AI PM 最该有的一种判断力**：不是所有场景都该上「自主 Agent」。
> 流程稳定的业务（取数、审批、报表）用「固定流水线」更可靠、更省钱、更好排查。
> 只有「路径无法预先穷举」的场景才值得交给 Agent 自主决策。

### 概念 7：Middleware（中间件）—— 「在 Agent 循环的钩子上做手脚」🔍

- **是什么**：官方把「Agent 循环」拆成几步（调模型 → 选工具 → 执行工具 → 再调模型……），
  **中间件就是在这些步骤前后挂的钩子**：改写提示词、裁剪可选工具、加重试 / 兜底、限流、
  护栏、PII 脱敏、对话摘要压缩、人工介入……官方内置了 `SummarizationMiddleware`、
  `HumanInTheLoopMiddleware` 等一批现成的。
- **本项目在哪**：官方路线是「`create_agent` + `middleware=[...]`」；本项目走**等价但手写**的形态 ——
  三道闸门（[chatbi.py](sqlagent/chatbi.py)）、重试封装（[llm.py](sqlagent/llm.py)）、
  改写回环（[workflow/graph.py](sqlagent/workflow/graph.py)）。**位置相同，实现方式不同。**
- **为什么重要**：中间件是官方推荐的「**能力加装点**」——把「重试、护栏、压缩、人工介入」从散落
  代码收成**可插拔的标准位**。这正是「Agent = Model + Harness」里 harness 的主要形态。

### 概念 8：Agent State & Memory（状态与记忆）🔍

- **是什么**：每个 Agent 自带一个**类型化字典** `AgentState` 装执行上下文；内置字段只有
  `messages`（整段对话历史，**只追加、不覆盖**）。要加自己的字段（`user_id`、计数器……），
  就**继承 `AgentState`**，用 `state_schema=` 传进 `create_agent`。
- **两类记忆**：**短期记忆** = 当前这条对话的历史（靠 `checkpointer` + `thread_id` 持久化与续聊）；
  **长期记忆** = 跨会话保留的用户偏好等。
- **本项目在哪**：手写实现了两处 ——
  - `WorkflowState`（[workflow/graph.py](sqlagent/workflow/graph.py#L44-L55)）就是本项目的「Agent State」；
  - `ChatBI` 的 `self.history` + `max_history=5` 就是**短期记忆**（[chatbi.py](sqlagent/chatbi.py#L112-L115)）。
- **为什么重要**：**没有状态，就没有「多轮」**。官方把状态标准化后，持久化 / 断点续跑 / 人工介入
  才能「免费」拿到（本项目的手写版做不到断点续跑）。

### 概念 9：Streaming（流式）🔍

- **是什么**：不憋一大段再吐，而是**边生成边输出**（`agent.stream(...)` / `astream(...)`）；
  可只流「最终文本」，也可流「每步事件 + 工具调用 + 状态变化」。
- **本项目在哪**：➖ **未用**（notebook / CLI 都是一次性返回）。
- **为什么重要**：**首字延迟**是 AI 产品体验的第一指标；链路越长越要流式，否则用户要干等十几秒。
  它也是「可观测」的前置。

### 概念 10：Context Management（上下文工程）🔍

- **是什么**：官方一句话——harness 的职责就是「**在对的时间，把对的上下文喂给模型**」；
  手段包括对话摘要压缩、动态提示词、只给相关工具、检索外部记忆。
- **本项目在哪**：🔍 部分体现 —— `schema_context(tables)` 只喂**选中表的 schema**（不是全库），
  `rewrite_followup` 只取最近 5 轮历史，都是「**控制上下文体积**」的做法；
  但**没有**官方的自动摘要压缩。
- **为什么重要**：上下文越长 ≠ 越好；**噪声会同时抬高成本、拉低准确率**。

### 概念 11：Fault Tolerance（容错）🔍

- **是什么**：重试（`tool_retry` / `.with_retry`）、模型兜底（`model_fallback`）、调用上限
  （`model_call_limit`）等；官方把它收进**中间件**与 **Runnable** 两层。
- **本项目在哪**：`build_chat_model()` 的 `with_retry(stop_after_attempt=3)` 与流水线的
  **改写回环**（`MAX_REWRITE=3`）——前者防**网络 / 接口**抖动，后者防**模型生成错 SQL**。
- **为什么重要**：LLM 调用天然不可靠；**没有容错的 Agent 不能上生产**。

### 概念 12：Guardrails & HITL（护栏与人工介入）🔍

- **是什么**：**护栏** = 拦非法 / 越权的输入输出（限流、PII 检测、越界拒绝）；
  **HITL（Human-in-the-loop）** = 关键动作**暂停等人工批准**（如「发邮件」「退款」「执行写操作」）。
  官方形态：`HumanInTheLoopMiddleware(interrupt_on={"send_email": True})`。
- **本项目在哪**：护栏已做（只读守卫 + 三道闸门，且**做进代码结构**，见概念 5 与面试 Q4）；
  HITL ➖ 未用，但 [workflow/graph.py](sqlagent/workflow/graph.py#L6-L8) 的注释已点明
  「接 `checkpointer` / `interrupt_before` 即可支持人工介入」。
- **为什么重要**：**「模型自主」与「业务可控」天生矛盾**，护栏与 HITL 就是那道闸。

### 概念 13：Observability（可观测性 / LangSmith）🔍

- **是什么**：把一次 Agent 运行看成**可回放的 trace** —— 每一步的提示词、模型输出、
  工具入参出参、状态流转、耗时与成本。官方用 LangSmith（`LANGSMITH_TRACING=true` 即接入）。
- **本项目在哪**：➖ LangSmith 未用；但项目自己的 **L0–L5 逐层裁判 + trace 字典**就是
  「不依赖平台的可观测」雏形（见 [README.md](README.md) 第 5 节）。
- **为什么重要**：Agent 出错时，**「哪一跳错了」比「错了」重要 100 倍**——这正是逐层评测的价值。

### 概念 14：Retrieval / RAG（嵌入与检索增强）🔍

- **是什么**：把外部知识（文档 / 库）先**检索**出来、拼进上下文、再让模型作答；
  核心件是 **Embeddings（嵌入）+ 向量库 + Retriever**。
- **本项目在哪**：➖ **本项目不是 RAG，而是 Text-to-SQL** ——它不从文档里「找相似答案」，
  而是把自然语言**编译成 SQL** 去查结构化数据。两者常被混淆，但一个是「检索相似文本」，
  一个是「生成并执行查询」。
- **为什么重要**：这是面试高频对比题；也是本项目后续若要接「表结构说明文档 / 指标口径库」时的自然扩展点。

#### 深入一层：`Embeddings` 抽象类 —— 为什么「实现它几乎零成本」

前面在 [概念 1](#深入一层为什么叫-chatopenai它能兼容-deepseek--qwen-吗) 讲过 `ChatOpenAI`：
它管的是**文字进、文字出**。而在 RAG 里还有一类角色管**文字进、向量出**——
它就是 `Embeddings`，LangChain 里与之对位的抽象类。

**它只规定了两个方法**（这就是「几乎零成本」的全部原因）：

```python
from langchain_core.embeddings import Embeddings

class Embeddings(ABC):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...  # 批量：建库时用
    def embed_query(self, text: str) -> list[float]: ...                   # 单条：用户提问时用
```

**为什么只要这两个方法就够了**：所有上层零件——向量库（Chroma、`InMemoryVectorStore`）、
检索器（Retriever）、混合召回与 RRF 融合、评测脚本——**都只调用这两个方法**，
它们**从不关心背后是阿里云、OpenAI 还是你手写的假函数**。

> **类比**：抽象类 = **插座规格**（两孔还是三孔），实现 = **具体电器**。
> 只要插头对得上，家里换台电器不需要改墙上的插座。
> 所以「能不能换向量服务」这个问题，在代码里的判据就是一句话：
> **上层有没有只依赖抽象类？** 依赖了，换模型就是换一个构造函数参数。

**在本项目（07）里为什么用不到**：07 是 Text-to-SQL，不向量化，所以全仓库
**没有一个 `Embeddings` 实现**——这正是它与 RAG 的分界线。

**在隔壁 08 项目（TikTok LIVE RAG）里，这套抽象立刻兑现成两笔收益**：

| 场景 | 实现类 | 收益 |
|---|---|---|
| 真实检索、跑评测 | `OpenAIEmbeddings`（把 `base_url` 指向 DashScope 国际站） | 只改 3 个参数就接入 `text-embedding-v4`，零自研代码 |
| 单元测试 `pytest` | 自己写 ~20 行的 `FakeEmbeddings(Embeddings)`，返回**确定性假向量** | 整条「切块 → 入库 → 召回 → 融合」链路**不打网络、毫秒级、离线可跑、零额度消耗** |

**PM 必须知道的一条「抽象类不保证」的隐形契约**：`embed_query` 与 `embed_documents`
**必须来自同一个模型、同一维度**。抽象类只管方法签名，**不管语义**——如果文档用 A 模型入库、
查询用 B 模型编码，两个向量不在同一个空间里，算出来的相似度是纯噪声。
这是典型的「**接口对得上，语义对不上**」，也是 RAG 效果差最常见的隐藏原因之一。

**面试一句话**：
> `Embeddings` 是 LangChain 管「文字 → 向量」的抽象类，只要求 `embed_documents` 和 `embed_query`
> 两个方法；上层全部只依赖这个抽象，所以换向量服务 = 换一个实现类。
> 它体现的是「**面向接口编程**」：真实后端与测试假后端可以互换，
> 但要注意查询与文档必须同模型同维度——**接口一致不等于语义一致**。

> 概念 7–14 本次**只立框架、给对照**；后续学习时我们再把每一条**逐个展开**（按你的兴趣挑顺序即可）。

### 附：Runnable 与 LCEL（一个技术名词，理解即可）

LangChain 把上面这些部件都统一成一种东西叫 **Runnable**（可运行单元），
并用 `|` 管道符把它们串起来，这就是 **LCEL**（LangChain Expression Language）：

```python
chain = prompt | model | parser      # 提示词 → 模型 → 输出解析
```

- **本项目在哪**：[llm.py](sqlagent/llm.py) 里 `build_chat_model()` 返回的就是一个 Runnable，
  可以 `.with_retry(...)`（重试）、`.with_fallbacks(...)`（降级）。
- **为什么重要**：这就是「标准化零件」的具体形态——只要都是 Runnable，就能自由组合。

> 顺带记一个**本项目踩过的真坑**（面试加分点）：`with_retry()` 之后的东西**不再是一个「模型」**，
> 而是一个「链路上的 Runnable」，所以它**没有** `with_structured_output()` 这种**模型专属**能力。
> 正确顺序是：**先用原生模型拿到结构化能力，再套重试**。见 [DESIGN.md](DESIGN.md#6-一次真实的-bugwith_retry-会丢模型级能力)。
> 这个坑说明：**框架抽象有层次，用错层次就会炸，而且离线假模型测不出来，必须做真实端到端验证。**

---

## 第三部分：举一反三（把这套思路迁移到别的产品）

任何一个「LLM + 业务数据 + 需要可控」的 AI 产品，骨架都是同一个：

```
① 定义能力（给哪些工具/数据）
② 定义契约（输入输出长什么样 —— 结构化输出）
③ 定义流程（固定流水线 or 自主 Agent —— LangGraph）
④ 定义护栏（安全、越界、失败降级 —— 代码结构，不是提示词）
⑤ 定义评测（带答案的验证集 —— 逐层裁判）
```

**迁移练习（换个场景，思路不变）**：

| 场景 | 工具（①能力） | 护栏（④安全边界） |
|---|---|---|
| 客服助手 | 查订单、查物流、发起退款 | 「退款」必须人工确认 / 金额上限 |
| 内部知识问答 | 检索文档库、查 Wiki | 只答已授权范围内的文档 |
| 数据分析助手（**本项目**） | 看表结构、执行只读 SQL | 只读守卫 + 越界问题反问 |
| 内容审核 | 分类模型、敏感词库 | 高风险必须人工复核 |

你会发现：**产品设计的重点，其实在「能力边界 + 安全护栏 + 评测」这三件事上**——
而不是「用了哪个模型」。这是 AI PM 与「调 API 的工程师」的分水岭。

---

## 第四部分：面试冲刺（AI PM 高频问题）

**Q1：LangChain 是什么？为什么用它？**
> 它是**构建 LLM 应用的工程框架**，不是模型。它把「调模型、拼提示词、要结构化输出、
> 重试、工具调用、流程编排」这些重复的胶水活标准化成通用部件，让应用**不被单一模型供应商锁定**、
> 更好维护和评测。LangChain v1 里，复杂编排交给了 LangGraph。

**Q2：LangChain 和 LangGraph 什么关系？**
> 一个是「零件箱」，一个是「装配图纸」。`create_agent` 内部就是用 LangGraph 实现的，
> **不是二选一**。

**Q3：Agent 和「固定工作流」怎么选？**
> 看**路径能不能预先穷举**。流程稳定、要审计要复现 → 固定工作流（本项目路线 A）；
> 路径开放、需要模型临场判断 → Agent（路线 B）。多数生产系统是**两者混合**。

**Q4：怎么保证 AI 不胡说/不越权？**
> **护栏做进代码结构，不是写进提示词**。本项目：只读守卫是正则白名单（不是「请只写 SELECT」）；
> 越界问题在生成 SQL 之前就被闸门拦下并反问。另外：结构化输出让下游可校验，失败显式降级而不猜。

**Q5：怎么评估一个 AI 应用好不好？**
> 用**带答案的冻结验证集**，而且是**逐层**评：本项目的 L0–L5 把失败钉到「选表 / 生成 / 执行 / 汇总」
> 具体哪一跳。原则是**能确定性判的绝不用 LLM 判**，只有「语义是否等价」才交 LLM，且**必须带参照物**。

**Q6：引入框架有什么代价？**
> 抽象层数变多（本项目的 Runnable 层次坑就是例子）、版本迭代快、调试链路变长。
> 所以**不是所有东西都该框架化**——纯安全逻辑、确定性判分，保持简单纯 Python（见 [DESIGN.md](DESIGN.md#2-边界不是所有东西都该-langchain-化)）。

**Q7：如果模型给你的是错的 SQL 怎么办？**
> 三重防线：① 只读守卫（结构性，先拦写操作）；② 试跑校验；③ 校验失败 → 带报错让模型**改写**，
> 最多 3 次；④ 仍失败就**诚实说「做不到」，绝不编数据**。这套「校验→改写→放弃」的闭环是生产必备。

---

## 附：本项目里最值得记住的 5 句话

1. **LLM 是大脑，LangChain 是手脚+记忆+流程。**
2. **换框架 ≠ 换行为**：提示词、重试次数、失败语义，一个都不该变。
3. **不是所有东西都该 LangChain 化**：安全逻辑与确定性判分，保持纯 Python。
4. **护栏做进代码结构，而不是提示词**——这是能不能上生产的红线。
5. **框架抽象有层次**：用错层次会炸，而且只有**真实端到端验证**才测得出来。

---

> 想亲手跑一遍？打开 [sql-agent-langchain.ipynb](sql-agent-langchain.ipynb)，
> 或直接 `python -m sqlagent.cli "数据库里一共有多少首歌曲？"`。
> 想深入设计取舍，读 [DESIGN.md](DESIGN.md)；想看你原来那版每一节搬到了哪，读 [MIGRATION.md](MIGRATION.md)。
