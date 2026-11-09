# 作业2：自建 3 个 MCP 工具并通过 Agent 调用

## 一、文件说明

| 文件 | 作用 |
|---|---|
| `work2_server.py` | MCP Server：定义工具（原有 2 个 + 新增 3 个），以 SSE 方式监听 8000 端口 |
| `work2_client.py` | MCP Client / Host：OpenAI Agent 连接上面的 Server，由大模型决定调用哪个工具 |

原有的 2 个工具来自课程 `Week10/2-fastmcp使用/2-兼容OpenAI-Agent/server.py`，保留做回归测试。

## 二、新增的 3 个工具

| # | 工具名 | 参数 | 说明 | 演示点 |
|---|---|---|---|---|
| 3 | `get_weather` | `city`, `days=1` | 查某城市未来几天天气 | 参数说明 + 取值范围约束(ge=1, le=7) + 默认值 |
| 4 | `convert_currency` | `amount`, `from_currency`, `to_currency` | 货币换算 | `Literal` 枚举约束（币种只能选 4 种） |
| 5 | `calculate` | `expression` | 四则运算 | 自定义工具名和说明 + 参数白名单校验 + 异常兜底 |

写 MCP 工具的四个要素（缺一不可）：

1. `@mcp.tool` 装饰器注册；
2. 参数**类型标注**（会被转成 JSON Schema 发给模型）；
3. **返回值类型**标注；
4. **docstring 就是工具说明书** —— 模型靠它判断什么时候该调用你。

## 三、运行方式（需要两个终端）

```powershell
# 终端 A：先启动 MCP Server，保持不关
cd "C:\Users\江海啸\Desktop\江海啸\c语言笔记\python_study\llm作业\llm第十周作业"
C:\anacond3\envs\py312\python.exe work2_server.py

# 终端 B：另开一个，运行 Agent 客户端
C:\anacond3\envs\py312\python.exe work2_client.py
```

> 注意：server 用的是 `transport="sse"`，所以客户端地址是 `http://localhost:8000/sse`。
> 如果改成 `transport="http"`，地址就要变成 `http://localhost:8000/mcp`，两者不能混用。

## 四、测试用例与预期

| 用户提问 | 预期调用的工具 | 考察点 |
|---|---|---|
| 最近有什么新闻？ | `get_news` | 原有工具回归 |
| 摇3次骰子得到结果。 | `roll_dice(n_dice=3)` | 参数提取 |
| 武汉未来3天的天气怎么样？ | `get_weather(city=武汉, days=3)` | 新工具 + 参数 |
| 北京和上海今天的天气，哪个更热？ | `get_weather` × 2 | 同一个工具调多次 + 结果比较 |
| 100 美元能换多少人民币？ | `convert_currency` | 枚举参数 |
| (12+8)*3 等于多少？ | `calculate` | 自定义工具名 |
| 武汉的天气怎么样？顺便算一下 25*4 是多少。 | `get_weather` + `calculate` | 一次提问触发多个工具 |

## 五、为什么新增工具不用改 client.py

`client.py` 里只有 `mcp_servers=[mcp_server]`，**没有列出任何具体工具名**。
SDK 在每次运行时通过 MCP 协议调用 `list_tools()` 动态获取工具清单
（agents SDK 里 `MCPServerSse` 的 `cache_tools_list` 默认为 `False`，即每次都重新拉取）。

所以：**工具在 server 侧增删，Agent 侧零改动**。这就是 MCP 的价值。

## 六、注意事项

1. **改完 server.py 必须重启 server**，否则客户端看到的还是旧的工具清单。
2. 工具名不能重复；参数类型要能转成 JSON Schema（str/int/float/bool/list/dict/Literal/Annotated 都可以）。
3. 工具内部的异常要自己捕获并返回错误文字，不要直接抛给 Agent。
4. docstring 写得越清楚，模型选工具越准；写含糊了模型可能永远不调用。
5. 只做验证工具是否注册，可以先用不经过大模型的方式：

```python
import asyncio
from fastmcp import Client

async def main():
    async with Client("http://localhost:8000/sse") as c:
        for t in await c.list_tools():
            print(t.name, "|", (t.description or "").split("\n")[0])

asyncio.run(main())
```