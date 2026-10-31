# OpenAI Agents SDK 学习手册

> 配套代码：本目录的 `main.py` / `experts.py` / `config.py`
> 实测环境：`openai-agents` 0.20.0 · Python 3.12 · 模型走 DeepSeek
> 写法原则：**每个结论都能在本地源码里查到**，第 5 节给出查证方法，不靠转述。

---

## 0. 先回答你的问题：这段是不是"专属语法"？

你选中的是：

```python
from agents import (
    Agent,
    HandoffOutputItem,
    RunContextWrapper,
    Runner,
    ToolCallItem,
)
from agents.mcp import MCPServer, MCPServerStdio, ToolFilterContext
```

**结论：不是 OpenAI Agents 的专属语法。要分三层看。**

| 层次 | 内容 | 属于谁 |
|---|---|---|
| **语法层** | `from X import Y` | **Python 语言本身**（PEP 328），跟任何 SDK 无关 |
| **命名空间层** | 包名叫 `agents`、子包 `agents.mcp` | openai-agents 这个库 |
| **语义层** | 这 5 个名字「是什么、怎么协作」 | SDK 的设计 ← **真正要学的部分** |

你觉得它"泛化"，是因为 **`from ... import ...` 天生泛化**——它只描述"从哪个命名空间取哪个名字"，不描述这些名字内部有多复杂。

一个反证，把包名换掉，语法完全同构：

```python
from json import dumps        # 标准库
from numpy import array       # 第三方数值库
from agents import Agent      # openai-agents
```

三者写法一模一样。所以**泛化来自 Python，不来自 SDK**。

---

### 但有一点要确认：这 5 个名字是 SDK 的公开 API

我核对过 `agents/__init__.py` 末尾的 `__all__` 列表，`"Agent"`、`"Runner"`、`"RunContextWrapper"`、`"HandoffOutputItem"`、`"ToolCallItem"` **都在其中**。

> `__all__` 是 Python 模块的"官方承诺":列在里面的名字，意味着作者承诺它们是稳定接口，不是随手写的内部实现。所以你可以放心 import。

---

### 更关键的一点：openai-agents **不是「OpenAI 专属」**

这是最容易误判的地方。三条证据：

1. `agents/models/interface.py` 里有抽象基类 `Model`（抽象方法 `get_response(...)`）和 `ModelProvider`（抽象方法 `get_model(model_name)`）——SDK 把"调用哪个模型"抽成了一个**可替换的接口**。
2. `agents/__init__.py` 导出了 `set_default_openai_client(client)`，允许你直接换掉底层客户端。
3. **我们这套 demo 跑的就是 DeepSeek**，不是 OpenAI 的模型。

它靠什么跑通的？靠"端点兼容 Chat Completions"这层兼容性——`config.py` 里那三行：

```python
os.environ["OPENAI_API_KEY"] = os.environ["DEEPSEEK_API_KEY"]
os.environ["OPENAI_BASE_URL"] = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
set_default_openai_api("chat_completions")   # 强制走 Chat Completions 而非 Responses
```

**准确说法是：SDK 是 OpenAI 出品的，但不绑定 OpenAI 的模型。**

---

## 1. 第一性原理：LLM 应用的最小内核

抛开一切框架，"能调工具的 LLM 应用"只有两件事：

**① 一次 HTTP 请求**
把 messages + 工具清单发给模型，拿回一段文本，或一个工具调用请求。

**② 一个循环**
如果模型要调工具 → 执行工具 → 把结果塞回 messages → **再发一次请求** → 直到模型不再要工具。

就这些。所有框架（LangChain / LlamaIndex / Agents SDK）干的都是给这两件事加包装。

**那 SDK 到底替你做了什么？** 看 `agents/run.py` 第 897 行：

```python
while True:
```

**这就是那个循环。它不是你的代码，是 SDK 的代码。**

从源码可见，这圈循环体内做了：

- 跑 input guardrails（区分并行 / 串行两种执行模式）
- 准备 sandbox、绑定当前 agent
- 调用模型
- 执行工具调用
- 处理 handoff（替换 `current_agent`）
- 累积 token usage

**"高效"的根源就在这**：手写时，这圈 while 的每一格都要你自己实现，而且边界情况极容易出错——工具执行报错怎么办？模型一次要调 3 个工具怎么办？handoff 之后历史消息怎么带上？SDK 把整圈循环压缩成一次 `await Runner.run(...)`。

---

## 2. 六个关键动作，逐个拆

### 2.1 `Agent` = 一份「声明」，不是「程序」

```python
Agent(
    name="FX Analyst",
    model=MODEL,
    instructions="你是外汇分析师……",
    mcp_servers=[server],
)
```

`Agent` 在 `agents/agent.py` 里是用 `@dataclass` 定义的。它**没有 `run()` 方法**——它只是一个配置容器。

> **理解点**：`Agent` 不干活，`Runner` 才干活。这个分工是整套 SDK 的骨架。

### 2.2 `Runner` = 那个循环的执行者

```python
result = await Runner.run(triage, q)
```

这一行背后就是第 1 节说的 `while True`。你交出去"起点（哪个 Agent）+ 输入"，收回来一份 `RunResult`。

**对比传统手写**：你得自己写 `client.chat.completions.create(...)`、自己判断 `finish_reason == "tool_calls"`、自己 `json.loads` 参数、自己 append `{"role": "tool", ...}`。这些全被吞掉了。

### 2.3 工具：从 Python 函数**自动**生成 JSON Schema

传统方式定义一个工具，你要手写这段 JSON：

```json
{
  "type": "function",
  "function": {
    "name": "get_weather",
    "parameters": {
      "type": "object",
      "properties": { "city": { "type": "string" } },
      "required": ["city"]
    }
  }
}
```

SDK 只要这样：

```python
@function_tool
def get_weather(city: str) -> str:
    """查询某城市的实时天气。"""
```

`agents/function_schema.py` 里的 `function_schema()` 会做三件事：

| 步骤 | 用到的机制 | 产出 |
|---|---|---|
| `get_type_hints(func)` | 读**类型注解** | 参数类型 |
| `inspect.signature(func)` | 读**函数签名** | 参数名、必填项 |
| `griffe` 解析 docstring | 读**文档字符串** | 给模型看的 `description` |

> **所以 SDK 里"类型注解"不是可选的装饰**——它是工具的接口契约，是喂给模型的 schema 来源。注解写错 = 模型看到错的 schema。

**这也解释了 MCP 工具怎么进来的**：MCP server 自己声明工具，SDK 通过 `MCPServerStdio` 把 schema 拉过来，和本地 `@function_tool` 生成的 schema 走同一条路。

### 2.4 `Handoff` = 交出控制权，**不是**函数调用

这是最容易理解错的一点。`handoffs=experts` 里每个专家，在模型眼里是一个**特殊工具**，但效果不是"调用并返回"，而是**换人**：

| | 工具调用 | **Handoff** |
|---|---|---|
| 谁在说话 | 还是原 Agent | **换成目标 Agent** |
| 之后用谁的 instructions | 原 Agent 的 | **目标 Agent 的** |
| 之后用谁的工具 | 原 Agent 的 | **目标 Agent 的** |
| 有没有"返回" | 有，控制权还回来 | **不还**，交接即完成 |

我们的 `Triage Agent` 之所以能"只分流、不干活"，就因为它 `handoffs=[...]` 但**不挂 `mcp_servers`**——它物理上没有工具可用，只能转交。

**证据**：`agents/items.py` 里 `HandoffOutputItem` 定义了 `source_agent` 和 `target_agent` 两个字段。我们的 `trace_of()` 就是读它们，打印出：

```
Triage Agent → FX Analyst
```

### 2.5 `RunItem` = 一次运行的「账本」

`result.new_items` 是一个列表，每一项都是运行过程中发生的一件事。`agents/items.py` 里的类型（实测清单）：

| 类型 | 含义 |
|---|---|
| `ToolCallItem` | 模型请求调用某个工具 |
| `ToolCallOutputItem` | 工具执行完毕，返回结果 |
| `HandoffCallItem` | 模型请求交接 |
| `HandoffOutputItem` | 交接已发生（带 source/target） |
| `MessageOutputItem` | 模型产出了一段给用户的文本 |
| `ReasoningItem` | 模型的推理过程（如支持） |
| `MCPListToolsItem` | 从 MCP server 拉取工具清单 |
| `MCPApprovalRequestItem` / `MCPApprovalResponseItem` | 工具调用的人工审批 |
| `ToolApprovalItem` | 普通工具的人工审批 |

注意 `HandoffCallItem` / `HandoffOutputItem` 和 `ToolCallItem` / `ToolCallOutputItem` 是**同构**的——"请求"与"结果"分开记。这不是巧合，说明 handoff 在框架内部就是被建模成一种特殊工具调用。

**和传统方式的差别**：手写时你只有 `messages` 数组，那是"**发给模型的东西**"，不是"**发生过什么**"的结构化记录。`new_items` 是后者——所以你能拿它做 UI、做审计、做测试断言。

### 2.6 `RunContextWrapper` = **不会**发给模型的那条侧信道

`agents/run_context.py` 的注释写得极直白：

> Contexts are not passed to the LLM. They're a way to pass dependencies and data to code you implement, like tool functions, callbacks, hooks, etc.

翻译：**`context` 里的东西永远不进 prompt。**

这是刚需。你要给工具传一个数据库连接、一个用户 session、一个 API client——这些既不该、也不能塞进 prompt（体积、安全、序列化三重问题）。SDK 的答案是：单独开一条通道，送进你自己的 Python 代码。

我们的 `route_tools(ctx, tool)` 用的就是它：

```python
def route_tools(ctx: ToolFilterContext, tool) -> bool:
    prefixes = EXPERTS.get(ctx.agent.name, {}).get("prefixes", ())
    return any(tool.name.startswith(p) for p in prefixes)
```

`ctx.agent` 就是「此刻正在要工具列表的那个 Agent」。所以**一个 server 连接 + 一个函数**，就能让 4 个专家各看一小块工具面——不需要开 4 个连接。

---

## 3. 对照表：传统手写 vs Agents SDK

| 动作 | 传统手写 | Agents SDK | 省在哪 |
|---|---|---|---|
| 工具 schema | 手写 JSON，改参数要同步改两处 | 类型注解 + docstring 自动生成 | 消灭"代码与 schema 不同步" |
| 多轮循环 | 自己写 while：判断 → 执行 → 回填 → 再请求 | `await Runner.run(agent, input)` | 消灭循环样板与边界 bug |
| 多个角色 | if/else 切 system prompt | `Agent` + `handoffs` | 角色变成**数据**，不是控制流 |
| 交接记录 | 从 messages 里猜 | `HandoffOutputItem` 结构化事件 | 可测试、可审计 |
| 权限隔离 | 自己写 if 判断 | 每个 Agent 挂不同工具 / `tool_filter` | 权限变成**声明** |
| 输出结构 | 自己写校验 + 重试 | `output_type=` | 消灭校验粘合代码 |
| 可观测性 | 自己打日志 | trace + `new_items` | 结构化事件流 |

**一句话总结这套「高效路径」**：

> 把**控制流**（while / if / else）换成**声明 + 数据**（Agent 配置 + 函数签名 + 专家表）。

这正是 `fin_triage_v2` 干的事——`experts.py` 是一张纯数据表，`main.py` 里**没有一行**"如果用户问汇率就……"。想换成机票/天气主题，只改 `experts.py` 一张表。

---

## 4. 三个反直觉点

**① 类型注解在 SDK 里是「运行时数据」，不是文档。**
平时 `def f(a: int)` 里的 `int` 运行时没什么用。但在 `@function_tool` 里，它会被 `get_type_hints()` 读出来**变成 JSON Schema**。

**② `Agent` 没有 `run()`。**
`Agent` 是 dataclass（配置），`Runner` 是引擎。想跑 Agent 必须找 Runner。这不是多余设计——它让"同一个 Agent 能被不同运行器复用"（比如批量跑、流式跑）。

**③ Handoff 不是「更强的工具调用」。**
工具调用会返回，Handoff 不返回。搞混了就会写出"转交之后还想取回结果"的错误代码。

---

## 5. 怎么自己查证（不靠我转述）

SDK 就装在本地，所有结论都能自查。先定位源码位置（用你运行项目的那支 Python）：

```bash
python -c "import agents, os; print(os.path.dirname(agents.__file__))"
```

> 定位到目录后，下面一律用**包内相对路径**引用，不含机器信息，可安全分享。

| 想验证什么 | 看哪里 |
|---|---|
| 那 5 个名字是不是公开 API | `agents/__init__.py` 末尾的 `__all__` 列表 |
| `Agent` 是 dataclass 吗 | `agents/agent.py` |
| 那个 `while True` 循环 | `agents/run.py` ≈ L897 |
| 工具 schema 怎么生成的 | `agents/function_schema.py` 的 `function_schema()` |
| Handoff 有哪些字段 | `agents/items.py` 的 `HandoffOutputItem` |
| 一共有哪几种 RunItem | `agents/items.py` 里所有 `class *Item` |
| context 会不会进 prompt | `agents/run_context.py` 的 `RunContextWrapper` docstring |
| 模型能不能换 | `agents/models/interface.py` 的 `Model` / `ModelProvider` |
| MCP 过滤怎么实现 | `agents/mcp/util.py` 的 `ToolFilterContext` |

---

## 6. 自测题

1. `from agents import Agent` 里的"泛化"来自哪里？
2. 如果给 `Triage Agent` 也加上 `mcp_servers=[server]`，会发生什么？
3. `RunContextWrapper.context` 里放的东西，模型看得到吗？
4. `ToolCallItem` 和 `ToolCallOutputItem` 有什么区别？为什么 handoff 也有对应的一对？
5. `experts.py` 里没有一行 `if/else`，路由是怎么发生的？
6. 为什么 `Agent` 被设计成 dataclass，而不是带 `run()` 方法的类？

---

## 附：本项目与文档的对应关系

| 概念 | 在本项目里的体现 |
|---|---|
| `Agent` 声明 | `main.py` 的 `build_triage()` 里那个列表推导 |
| `Runner` 循环 | `main.py` 的 `await Runner.run(triage, q)` |
| Handoff 交出控制权 | `Triage Agent` 只挂 `handoffs`、不挂 `mcp_servers` |
| 交接的事件记录 | `main.py` 的 `trace_of()` 读 `HandoffOutputItem` |
| 侧信道 context | `main.py` 的 `route_tools(ctx, tool)` 读 `ctx.agent` |
| 工具 schema 复用 | 4 个专家共享同一个 `MCPServerStdio` 实例 |
| 声明式数据表 | `experts.py` 整张 `EXPERTS` dict |
