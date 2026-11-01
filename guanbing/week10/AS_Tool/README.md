# fin_triage_v2_astool —— 把 `handoffs` 换成 `as_tool` 的 A/B 对照实验

> **一句话**：同一组专家、同一批数据、同一份配置，**只改一行接线** —— 把 `handoffs=experts`
> 换成 `tools=[e.as_tool(...) for e in experts]` —— 用来验证一条关于「转述会不会把数字写错」的推理。

这是 [`../fin_triage_v2`](../fin_triage_v2) 的**对照实验版**，定位是**教学 / 实验产物**，不是一个新产品版本。

支持的四个方向与 v2 完全一致：**法定货币汇率 / 加密资产 / 股指与商品行情 / 宏观经济**。

---

## 1. 这是什么，不是什么

| | 说明 |
|---|---|
| **是什么** | 一个 **A/B 对照实验**：在完全相同的条件下，只替换「多 Agent 的接线方式」，观察输出差异 |
| **不是什么** | 不是 v2 的升级版、不是 v3。它**故意保留了实验性代码**（并排打印两段输出），不适合直接当生产模板 |

与 v2 的差异就这么大：

```diff
  triage = Agent(
      name="Triage Agent",
      model=MODEL,
      instructions=...,
-     handoffs=experts,                                  # v2：交控制权（换人）
+     tools=[e.as_tool(...) for e in experts],           # 本目录：当工具调（派人办事）
  )
```

`config.py` 与 `experts.py` **一行未动**——这是实验可信度的前提：差异只能来自接线方式，不能来自别的变量。

---

## 2. 起因：要验证一条推理

在 [`../fin_triage_v2/OpenAI-Agents-学习手册.md`](../fin_triage_v2/OpenAI-Agents-学习手册.md) 里，
曾写下这样一条论断（**推理，不是实测**）：

> 「选 `handoffs` 的理由：`as_tool` 模式下，专家的答案要经过编排者改写一轮，
> 改写的每一步都可能把数字写错。」

这条论断**听起来很合理，但当时没有任何实验支撑**。这个目录就是为了把它变成证据（或反例）。

**实验设计的关键点**：为了让对照在**同一次运行、同一批数据**上完成，本版本额外做了一件事——
把**子 Agent 的原话**和**编排者的转述**同时打印：

| 打印项 | 取自 | 含义 |
|---|---|---|
| 【子 Agent 原文】 | `ToolCallOutputItem.output` | 工具返回值，就是子 Agent 的 `final_output` |
| 【A：编排者转述】 | `result.final_output` | 编排者自己写的那段（最终给用户的） |

两段并排看，就能直接判定「数字有没有在转述中漂移」。这比拿两次运行的数字去比要干净得多——
数据源完全相同，唯一变量就是「多出来的那一轮生成」。

> 结论提前说：**这条推理被推翻了一半**，详见 [第 6 节](#6-实测结论ab-结果)。

---

## 3. 核心机制：`as_tool` 到底做了什么

这是本项目的**主角**，也是理解本目录的全部意义所在。

### 3.1 官方 docstring 里的两条差异

`as_tool()` 的 docstring（`agents/agent.py`）写得极直白，原文两条：

> This is different from handoffs in two ways:
> 1. In handoffs, the new agent receives the conversation history.
>    In this tool, the new agent receives **generated input**.
> 2. In handoffs, the new agent **takes over** the conversation.
>    In this tool, the conversation is **continued by the original agent**.

翻译过来就是：

| | `handoffs` | `as_tool` |
|---|---|---|
| 子 Agent 拿到什么 | **完整对话历史** | 只拿到**本次生成的 input**（默认就是用户那句话） |
| 谁继续对话 | **子 Agent 接管**，父 Agent 退场 | **父 Agent 继续**，子 Agent 只是被叫去办事 |

### 3.2 内部机制：再起一次 `Runner.run`

`as_tool()` 返回的是一个 `FunctionTool`。它被执行时，内部做的事情是**再起一次子运行**：

```python
# agents/agent.py（包内相对路径）
run_result = await Runner.run(starting_agent=self, input=resolved_input, ...)
...
return run_result.final_output          # 子 Agent 的最终输出，作为「工具返回值」回填
```

可查证的事实：

| 事实 | 证据位置（包内相对路径） |
|---|---|
| 默认入参 schema 是 `{"input": str}` | `agents/agent_tool_input.py` 的 `class AgentAsToolInput` |
| 子 Agent 是**独立一次 run**，不共享父对话历史 | 上面 docstring 第 1 条 |
| 返回值 = 子 Agent 的 `final_output`，回填成父 run 的一条工具输出 | `agents/agent.py` 的 `return run_result.final_output` |
| 每次调用在父 run 里表现为 `ToolCallItem` + `ToolCallOutputItem` | `run_internal/tool_execution.py` 的 `_build_function_tool_results()` |

**一句话记住**：

> **`handoffs` 是「换人」，`as_tool` 是「派人去办事，办完回来汇报」。**

### 3.3 一张表两面看

| 维度 | `fin_triage_v2`（`handoffs`） | 本目录（`as_tool`） |
|---|---|---|
| 接线 | `handoffs=experts` | `tools=[e.as_tool(...)]` |
| **控制权** | **交出去**，不再收回 | **不交**，编排者始终在场 |
| 子 Agent 的历史视野 | 继承父的全部历史 | 只见到本次生成的 input |
| 子 Agent 的产出 | 直接成为**最终回答** | 降级为一条**工具返回值**，可被再加工 |
| 能否多步 | 一次交接服务一个专家 | 可以**串行调用多个**再汇总 |
| 父 run 的事件类型 | `HandoffCallItem` / `HandoffOutputItem` | `ToolCallItem` / `ToolCallOutputItem` |
| 最终回答的口吻 | **是子 Agent 说的** | **是编排者说的** |
| 本项目里的验证方法 | `trace_of()` 读 `HandoffOutputItem` | `trace_of()` 读 `ToolCallItem` / `ToolCallOutputItem` |

> 顺带一个结构上的发现：`HandoffCallItem` / `HandoffOutputItem` 与
> `ToolCallItem` / `ToolCallOutputItem` 是**同构**的（都是「请求」+「结果」成对出现）。
> 这不是巧合——说明框架内部确实把 handoff 建模成了一种特殊工具调用。

### 3.4 必须知道的细节：工具名会被自动改写

本目录调用 `as_tool` 时**没有显式给 `tool_name`**：

```python
expert.as_tool(tool_name=None, tool_description=str(spec["handoff_description"]))
```

于是工具名从 Agent 名转换而来，启动时会看到 4 行 warning：

```
Tool name 'FX Analyst' contains invalid characters for function calling
and has been transformed to 'fx_analyst'.
...
```

最终模型看到的接口名是转换后的：`fx_analyst` / `crypto_analyst` / `market_analyst` / `macro_analyst`。

> **教训**：工具名是**给模型看的接口名**，不是内部变量名。含空格/大写会被静默改写，
> 而且改写规则（空格转下划线、转小写）不一定符合你的预期——想要确定的接口名，就**显式传 `tool_name`**。

另外注意 `tool_description` 用的是 `spec["handoff_description"]`：

> **`handoff_description` ↔ `tool_description` 是同一个东西的两种接线**——都是「写给主 Agent 看的一句话」，
> 作用都是让主 Agent 判断该不该叫它。这也是为什么这次实验能干净地「只改一行」。

---

## 4. 架构

```
                        ┌──────────────────────────────┐
   用户提问 ──────────▶  │    Triage Agent（编排者）      │  ← 不挂 mcp_servers
                        │  tools = 4 个 agent-tool      │     只有 4 个「工具」
                        └───────────────┬──────────────┘
                                        │ 工具调用（不是交接）
          ┌──────────────┬──────────────┼──────────────┬──────────────┐
          ▼              ▼              ▼              ▼
    fx_analyst    crypto_analyst   market_analyst  macro_analyst
   （包装 FX     （包装 Crypto     （包装 Market    （包装 Macro
     Analyst）      Analyst）        Analyst）        Analyst）
          └──────────────┴──────────────┴──────────────┴──────────────┘
                                        │ 每个子 Agent 内部各自再起一次 Runner.run
                                        │ 4 个子 Agent 共享同一个 MCPServerStdio 实例
                                        ▼
                          fin-data-mcp（83 个工具，免密钥）
                                        │
                                        ▼
                        返回值 = 子 Agent 的 final_output
                                        │
                                        ▼
                回到编排者手里 → 编排者自己写最终答案（控制权从未交出）
```

关键点：

- **一个 server 实例**：4 个子 Agent 共享同一个 `MCPServerStdio`，只起一个 `npx` 子进程；
  与 v2 完全相同的动态 `tool_filter` 让每个专家只看自己 `prefixes` 命中的工具。
- **过滤是按 Agent 现算的**：`cache_tools_list=True` 缓存的是「过滤前」的全量列表，
  过滤在每个 Agent 请求时重新执行，所以能按 `ctx.agent.name` 给出不同视野。
- **控制权不交出**：每次调用子 Agent 都是**全新一次子运行**，父 run 一直在场，
  这就是 `as_tool` 与 `handoffs` 最本质的分野。

---

## 5. 如何使用

### 5.1 前置条件

| 依赖 | 用途 | 检查方式 |
|---|---|---|
| Python 3.10+ | 运行 | `python -V` |
| Node.js（含 `npx`） | 启动 MCP server | `npx -v` |
| `openai-agents`、`python-dotenv` | 运行依赖 | `pip show openai-agents python-dotenv` |

脚本启动时会自动检查 `npx`，缺失则直接提示「请先安装 Node.js」。

### 5.2 配置密钥

密钥**只放在 `.env` 里**（本目录或任意上级目录均可，`config.py` 会从本文件所在目录**逐级向上**查找）。
最少写一行：

```dotenv
DEEPSEEK_API_KEY=你的密钥
```

可选覆盖项（不写则用默认值）：

```dotenv
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEEPSEEK_MODEL=deepseek-chat
```

> 仓库里**没有任何密钥**。`.env` 已被 `.gitignore` 忽略，见 [第 8 节](#8-安全须知)。

### 5.3 运行

```bash
python main.py
```

> 依赖链：`config.py` 会走到 `agents` 的 import，所以 `main.py` 的第一行必须是
> `from config import MODEL, SERVER_PARAMS`（详见 [第 9 节](#9-一条容易踩的不变量)）。

### 5.4 预期输出

启动先打印**工具面自检**，证明过滤依然生效（数字为本机实测值，与 v2 一致）：

```
================================================================
工具面自检（同一个 server 实例，按 Agent 过滤）
  (不过滤)           83 个
  FX Analyst         5 个
  Crypto Analyst     13 个
  Market Analyst     10 个
  Macro Analyst      15 个
================================================================
编排者可用的 agent-tool: fx_analyst, crypto_analyst, market_analyst, macro_analyst
```

随后依次跑 4 条用例，**每条并排打印两段**：

```
================================================================
Q: 现在美元兑人民币的汇率是多少？
调用链: fx_analyst
接手: Triage Agent   ← 注意：控制权没有交出去
new_items 条数: 4
----------------------------------------------------------------
【子 Agent 原文】（工具返回值）
## 美元兑人民币（USD/CNY）最新汇率
...
----------------------------------------------------------------
【A：编排者转述】（最终交给用户的那段）
美元兑人民币的最新汇率如下：
...
```

四条用例对应的预期调用链：

| 问题 | 期望调用 | 期望接手 |
|---|---|---|
| 现在美元兑人民币的汇率是多少？ | `fx_analyst` | **Triage Agent** |
| 比特币（BTC）现在多少钱？ | `crypto_analyst` | **Triage Agent** |
| 纳斯达克指数现在多少点？顺便看下黄金和原油价格。 | `market_analyst` | **Triage Agent** |
| 中国最新的 GDP 增速是多少？ | `macro_analyst`（可能多次） | **Triage Agent** |

> **看输出时先看 `接手:` 这一行。** 4 条全部是 `Triage Agent`，就是 `as_tool` 生效的铁证——
> 换成 v2 跑，这里会变成 `FX Analyst` / `Crypto Analyst` 等。

### 5.5 换成你自己的专家

1. 编辑 `experts.py` 的 `EXPERTS` 表（加一条 = 加一个专家，`as_tool` 会自动多一个 agent-tool）；
2. 确认新专家的 `prefixes` 与 MCP server 的工具名前缀一致；
3. 想换测试用例，改 `main.py` 里的 `QUESTIONS`。

`config.py` 和 `main.py` 都不用动。

---

## 6. 实测结论（A/B 结果）

### 6.1 被推翻的一半：**数字零漂移**

翻出日志逐字比对子 Agent 原文与编排者转述：

| 问题 | 子 Agent 原文里的数字 | 编排者转述里的数字 | 结论 |
|---|---|---|---|
| Q1 汇率 | `6.6529` / `6.670992` / `6.653209` | `6.6529` / `6.670992` / `6.653209` | **逐个一致** |
| Q2 比特币 | `78,120.8`、`77,453.4`、`78,251.1`、`77,333.3` … | 表格逐格一致 | **逐个一致** |
| Q3 纳指+金+油 | 纳指 / 黄金 / 原油表格 | 逐格一致 | **逐个一致** |

> **原断言「转述多一轮改写，改写的每一步都可能把数字写错」没有被证实。**
> 原因不难猜：编排者的 instructions 里明确写了「数字必须原样搬运」，
> 而**原始数字就在它的上下文里**（工具返回值）——逐字抄写是个低难度动作，不是「重新计算」。

### 6.2 换了一个面貌的另一半：**细节被裁剪**

数字虽然没动，但**信息的完整度被裁掉了**：

| 被裁掉的东西 | 举例 |
|---|---|
| 标题 / 结构 | 子 Agent 的 `## 行情快照` 等标题被换掉或简化 |
| 来源与出处说明 | 「以上数字均来自 OKX BTC-USDT 现货 ticker 返回原文」**整句消失** |
| 分析段落 | Q3 的 market 分析由 **4 段压成 3 段** |
| 不确定性提示 | 「我没有取到宏观数据，这只是一个基于价格结构的推断」这句 **hedging 被删掉** |

> **这才是 `as_tool` 在这个实验里真实的代价：不是数字错，而是「专家说的限定条件被前台省略了」。**
> 对金融场景来说这未必更轻——**一次没有说清来源与假设的数字，比一个抄错的数字更危险**，
> 因为它看起来同样确定。

### 6.3 一个反转：`as_tool` 自带「把关层」

Q4 里 `macro_analyst` 被调用**两次**，两次都返回 `Error: Max turns (10) exceeded`。
编排者的表现是：

> **如实报告工具失败，并明确拒绝凭空给出增速数字。**

如果换成 `handoffs`，控制权早就交给了专家，且**没有任何人复核它的输出**。
`as_tool` 多出的那一层，给了编排者一次**发现异常并选择不糊弄**的机会。

> **所以两个模式的代价清单是对称的**：
> `handoffs` 拿到「结构保证」（前台物理上无工具，没有渠道编数字），但**放弃了把关层**；
> `as_tool` 拿到把关层与跨域汇总能力，但**放弃了「专家亲自作答」**。

---

## 7. 从第一性原理出发：怎么选

抛开术语，两个模式真正不同的只有**两件事**：

1. **最终那段话由谁写**——谁持有产出 token 的权力？
2. **子 Agent 能不能看见历史**——它是「接手一段对话」还是「接一个任务」？

其余差别（事件类型、能否多步汇总、格式统不统一）**都是这两件事的推论**。

| 你的诉求 | 内因 | 选择 |
|---|---|---|
| 专家**必须亲自**回答，前台连改写的机会都不该有 | 最终话语权交给专家 | **`handoffs`** |
| 要**物理上**堵死「前台编数字」这条路 | 前台没有产出权 | **`handoffs`** |
| 一次要办**好几件事**，再汇总成一份回答 | 需同一方连续调多次 | **`as_tool`** |
| 需要**统一格式**（各专家口吻/结构五花八门） | 由编排者收口 | **`as_tool`** |
| 需要**复核**子 Agent 输出、拦截失败 | 编排者天然是一道关 | **`as_tool`** |
| 要**多轮对话**、上下文延续给专家 | 子 Agent 要能看见历史 | **`handoffs`** |

**一句话**：**要「换人」就用 `handoffs`，要「派人办事再汇总」就用 `as_tool`。**

> **最后一条经验（重要）**：别把「选 handoff 还是 `as_tool`」当成**准确率的主开关**。
> 本实验证明它在**数字层面几乎不动分毫**，真正的差异在「谁来收口」和「谁被省略了什么」。
> 想提升数字准确率，优先级应该是：
> **① 收窄工具面 > ② 结构化输出 + 校验 > ③ 模式选择**。

---

## 8. 安全须知

这是上传到公开仓库前必须守住的底线：

- **密钥只存在于 `.env`**，代码里一律通过 `os.environ` 读取，**没有任何硬编码 Key**。
- 本目录**不含任何机器相关的绝对路径**：`config.py` 的报错信息用的是运行时计算的搜索起点
  （`_HERE`），不会固化开发者的本地目录结构。
- 本目录下**不存在 `.env` 文件**（只有 `.env.example` 模板，且 Key 为空）。
- `.gitignore` 已就位：

  ```gitignore
  .env
  .env.*
  !.env.example
  !.env.*.example
  __pycache__/
  *.py[cod]
  .DS_Store
  ```

- 上传前建议自查两遍（在仓库根目录执行）：

  ```bash
  # ① 有没有明文密钥
  git grep -nE "sk-[0-9a-zA-Z]{16,}|api[_-]?key\s*=" -- . | grep -v "\.env\.example"

  # ② 有没有本地绝对路径（正则写成字符类的形式，避免把本条命令自己也扫出来）
  git grep -nE "/(Users|home)/" -- .
  ```

---

## 9. 一条容易踩的不变量

`main.py` 的第一行 **必须** 是：

```python
from config import MODEL, SERVER_PARAMS
```

原因：Agents SDK 是**运行时**才去读 `OPENAI_API_KEY` 和 `set_default_openai_api` 这类全局开关，
而 `config.py` 负责在它读之前把环境变量设好。Python 导入一个模块时会先把它整个执行完，
所以只要 `config` 是 import 链的第一环，顺序就一定正确。

好在 `MODEL` 正是从 `config` 来的——你没法「忘记」import 它。

---

## 10. 目录结构

```
fin_triage_v2_astool/
├── README.md          # 本文件：实验目的、as_tool 机制、实测结论
├── config.py          # 配置层：环境 → DeepSeek → SDK 开关 → MCP 参数（与 v2 相同）
├── experts.py         # 数据层：EXPERTS 表（纯数据，零 import）（与 v2 相同）
├── main.py            # 逻辑层：as_tool 组装 / 并排打印两段输出 / 运行
├── .env.example       # 密钥模板（占位符，无真实 Key）
└── .gitignore         # 忽略 .env 与运行时产物
```

**对照阅读顺序建议**：

1. 先跑一遍本目录，看 `接手: Triage Agent` 与并排的两段输出；
2. 再读 [`../fin_triage_v2/README.md`](../fin_triage_v2/README.md) 了解 `handoffs` 版本；
3. 最后读 [`../fin_triage_v2/OpenAI-Agents-学习手册.md`](../fin_triage_v2/OpenAI-Agents-学习手册.md)
   的第 7、8 节——那里有完整的源码级对照与选择依据。

---

## 11. 已知限制

- **这是实验代码，不是生产模板**：并排打印两段输出的逻辑只服务于对照观察。
- **MCP server 冷启动**：首次 `npx` 拉取/构建较慢，`client_session_timeout_seconds` 设为 120 秒兜底。
- **`as_tool` 默认不传对话历史**：子 Agent 只拿到本次 input。所以本实验里
  「前一轮聊过什么」对专家不可见——这正是 v2 用 `handoffs` 的理由之一。
- **`tool_name=None` 会触发改名 warning**：想要确定的接口名，请显式传 `tool_name`。
- **DeepSeek 的 DSML 标签泄漏**：模型偶尔会把工具调用标记（`<｜｜DSML｜｜...`）写进正文，
  `main.py` 的 `clean()` 做正则兜底清洗。这是模型侧的兼容问题，不是本项目的设计。
- **无 pytest**：验证靠启动自检与 4 条用例的并排打印，尚未沉淀为自动化测试。
- **`macro_analyst` 可能触发 `Max turns exceeded`**：本实验中它被调用两次均失败，
  这属于 MCP 数据源侧的稳定性问题，非 `as_tool` 机制缺陷。
