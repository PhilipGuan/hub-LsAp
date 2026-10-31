# fin_triage_v2 —— 金融行情 Triage Agent（模块化版）

一个用 [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/) + MCP 搭起来的多 Agent 命令行 Demo：
一个「前台分流器」（Triage Agent）判断你问的是哪一类金融问题，把控制权交给对应的专家 Agent 去查数据、给答案。

支持的四个方向：**法定货币汇率 / 加密资产 / 股指与商品行情 / 宏观经济**。

---

## 1. 项目起因

项目起点是同目录下的单文件版 `../fin_triage_example/main.py`（约 200 行）。

单文件版跑通之后暴露了一个具体问题：**三类完全不同的东西挤在一个文件里**——

- 环境变量加载、DeepSeek 接入、Agents SDK 全局开关、MCP server 启动参数（**配置**）
- 四个专家的名字、工具前缀、路由说明、作业指令（**业务数据**）
- 动态工具过滤、Agent 组装、结果清洗、测试用例（**逻辑**）

它们的变化理由完全不同：换模型要改第一类，加专家要改第二类，改路由要改第三类。
放在一起的结果就是——**每加一个专家都要在一个 200 行的文件里找到正确的段落去改**，
很容易漏改（比如加了专家却忘了在 Triage 的指令里补一行路由线索）。

于是有了这个 v2：**先确认单文件版行为正确，再原样拆开，最后用同一批测试用例验证行为没变。**

---

## 2. 创立思路

### 2.1 按「变更原因」切，不按行数切

拆分的唯一标准是「什么情况下需要改这个文件」：

| 文件 | 什么时候改它 | 里面是什么 |
|---|---|---|
| `config.py` | 换模型 / 换 Key / 换 MCP server | 环境与全局配置 |
| `experts.py` | 增删专家、调工具前缀、改指令 | 纯数据表，零 import |
| `main.py` | 改路由逻辑、改展示格式、换测试用例 | 执行逻辑 |

### 2.2 泛化抓手是 `experts.py` 这张表

`experts.py` 里只有一个字典 `EXPERTS`，每个专家由三个字段描述：

- `prefixes`：这个专家能看到哪些 MCP 工具（前缀匹配，决定工具面）
- `handoff_description`：写给 Triage Agent 看的路由线索
- `instructions`：接手后给专家自己看的作业指导

`main.py` 的 `build_triage()` **从这张表自动生成** Triage Agent 的路由指令。
所以「新增一个专家」= 在表里加一条，不需要动 `main.py` 的任何一行逻辑，也不会出现
「专家定义了但前台不知道」的脱节。这就是 DRY 的落点。

### 2.3 收窄工具面：一个 server，四种视野

MCP server（`fin-data-mcp`）一共暴露 83 个工具。把 83 个工具全塞给每个 Agent 有两个坏处：
prompt 变长、模型选错工具的概率变高。

v2 用 **动态 `tool_filter`** 解决：同一个 `MCPServerStdio` 实例被 4 个专家共享（只起一个子进程），
但每个 Agent 请求工具列表时，只看到自己 `prefixes` 前缀命中的那几个。

### 2.4 权限隔离：前台只挂 `handoffs`

Triage Agent **只挂 `handoffs`，不挂 `mcp_servers`**。它在物理上没有任何工具可调，
只能判断意图然后转交——想「顺手帮你查个数」也做不到。这是刻意的权限设计，不是疏忽。

---

## 3. 架构

```
                        ┌──────────────────────────┐
   用户提问 ──────────▶  │      Triage Agent        │  ← 无 mcp_servers，只有 handoffs
                        │  （按 EXPERTS 自动生成指令）│
                        └────────────┬─────────────┘
                                     │ handoff（交控制权，不返回）
        ┌──────────────┬─────────────┼─────────────┬──────────────┐
        ▼              ▼             ▼             ▼
   FX Analyst   Crypto Analyst  Market Analyst  Macro Analyst
   (fx_*)       (crypto_*)      (yahoo_*,       (worldbank_*,
                                 cboe_*)         oecd_*, dbnomics_*)
        └──────────────┴─────────────┴─────────────┴──────────────┘
                                     │ 共享同一个 MCPServerStdio 实例
                                     ▼
                       fin-data-mcp（83 个工具，免密钥）
```

关键点：

- **一个 server 实例**：4 个专家共享，只起一个 `npx` 子进程。
- **过滤是按 Agent 现算的**：`cache_tools_list=True` 缓存的是「过滤前」的全量列表，
  过滤在每个 Agent 请求时重新执行，所以能按 `ctx.agent.name` 给出不同视野。
- **Handoff 是交控制权**：接手专家用自己的 instructions 和工具集，不会「回到」前台。

---

## 4. 如何使用

### 4.1 前置条件

| 依赖 | 用途 | 检查方式 |
|---|---|---|
| Python 3.10+ | 运行 | `python -V` |
| Node.js（含 `npx`） | 启动 MCP server | `npx -v` |
| `openai-agents`、`python-dotenv` | 运行依赖 | `pip show openai-agents python-dotenv` |

脚本启动时会自动检查 `npx`，缺失则直接提示「请先安装 Node.js」。

### 4.2 配置密钥

密钥**只放在 `.env` 里**（项目根目录或任意上级目录均可，`config.py` 会逐级向上查找）。
最少写一行：

```dotenv
DEEPSEEK_API_KEY=你的密钥
```

可选覆盖项（不写则用默认值）：

```dotenv
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEEPSEEK_MODEL=deepseek-chat
```

> 仓库里**没有任何密钥**。`.env` 已被 `.gitignore` 忽略，见第 6 节。

### 4.3 运行

在 `fin_triage_v2/` 目录下：

```bash
python main.py
```

### 4.4 预期输出

启动先打印**工具面自检**，证明过滤真的生效（数字为本机实测值）：

```
================================================================
工具面自检（同一个 server 实例，按 Agent 过滤）
  (不过滤)           83 个
  FX Analyst         5 个
  Crypto Analyst     13 个
  Market Analyst     10 个
  Macro Analyst      15 个
================================================================
```

随后依次跑 4 条测试用例，每条打印路由、调用的工具、接手 Agent 和答案：

```
----------------------------------------------------------------
Q: 现在美元兑人民币的汇率是多少？
路由: Triage Agent → FX Analyst
工具: fx_latest_rates
接手: FX Analyst
A: ...
```

四条用例对应的预期路由：

| 问题 | 期望接手 |
|---|---|
| 现在美元兑人民币的汇率是多少？ | FX Analyst |
| 比特币（BTC）现在多少钱？ | Crypto Analyst |
| 纳斯达克指数现在多少点？顺便看下黄金和原油价格。 | Market Analyst |
| 中国最新的 GDP 增速是多少？ | Macro Analyst |

### 4.5 换成你自己的专家

1. 编辑 `experts.py` 的 `EXPERTS` 表（加一条 = 加一个专家）；
2. 确认新专家的 `prefixes` 与 MCP server 的工具名前缀一致；
3. 想换测试用例，改 `main.py` 里的 `QUESTIONS`。

`config.py` 和 `main.py` 都不用动。

---

## 5. 一条容易踩的不变量

`main.py` 的第一行 **必须** 是：

```python
from config import MODEL, SERVER_PARAMS
```

原因：Agents SDK 是**运行时**才去读 `OPENAI_API_KEY` 和 `set_default_openai_api` 这类全局开关，
而 `config.py` 负责在它读之前把环境变量设好。Python 导入一个模块时会先把它整个执行完，
所以只要 `config` 是 import 链的第一环，顺序就一定正确。

好在 `MODEL` 正是从 `config` 来的——你没法「忘记」import 它。

---

## 6. 安全须知

这是上传到公开仓库前必须守住的底线：

- **密钥只存在于 `.env`**，代码里一律通过 `os.environ` 读取，没有任何硬编码 Key。
- 仓库内**不含任何机器相关的绝对路径**：`config.py` 的报错信息用的是运行时计算的
  搜索起点（`_HERE`），不会固化开发者的本地目录结构。
- `.gitignore` 已就位，忽略：

  ```gitignore
  .env
  .env.*
  !.env.example
  !.env.*.example
  __pycache__/
  *.py[cod]
  .DS_Store
  ```

  如果你要提交 `.env.example` 作为模板，记得里面只放**占位符**，不要放真实密钥。
- 上传前建议自查一遍：

  ```bash
  git grep -nE "sk-[0-9a-zA-Z]{16,}|api[_-]?key|token|secret" -- . | grep -v "\.env\.example"
  ```

---

## 7. 目录结构

```
fin_triage_v2/
├── README.md                  # 本文件：起因、思路、如何使用
├── OpenAI-Agents-学习手册.md   # 原理向：SDK 的关键动作与第一性原理
├── config.py                  # 配置层：环境 → DeepSeek → SDK 开关 → MCP 参数
├── experts.py                 # 数据层：EXPERTS 表（纯数据，零 import）
├── main.py                    # 逻辑层：tool_filter / build_triage / 运行
└── .gitignore                 # 忽略 .env 与运行时产物
```

**README（本文件）负责「怎么跑起来」，学习手册负责「为什么这样写」。**
想看 Handoff 与工具调用的本质区别、`RunContextWrapper` 为什么不进 prompt、
`function_schema()` 怎么把 Python 函数变成 JSON Schema，请看
[OpenAI-Agents-学习手册.md](./OpenAI-Agents-学习手册.md)。

---

## 8. 已知限制与技术债

- **MCP server 冷启动**：首次 `npx` 拉取/构建较慢，`client_session_timeout_seconds` 设为 120 秒兜底。
- **DeepSeek 的 DSML 标签泄漏**：模型偶尔会把工具调用标记（`<｜｜DSML｜｜...`）写进正文，
  `main.py` 的 `clean()` 做正则兜底清洗。这是模型侧的兼容问题，不是本项目的设计。
- **`triage_core.py` 尚未抽取**：目前 Triage + tool_filter 的模式在金融版和 git 版里各写了一份，
  真正跨 Demo 的泛化还没做。
- **无 pytest**：验证靠 `main.py` 里的启动自检与 4 条用例打印，尚未沉淀为自动化测试。
