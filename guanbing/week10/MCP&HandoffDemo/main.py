"""金融行情助手 v2 —— 把「配置」和「专家数据」拆出去后的版本。

三个文件的分工（按「变更原因」切，不是按行数切）：
    config.py   ：换模型 / 换 Key / 换 MCP server 时改这里
    experts.py  ：增删专家、调工具前缀、改指令时改这里
    main.py     ：改路由逻辑、改展示格式、换测试用例时改这里

拆完之后的泛化收益：main.py 里已经没有任何「业务数据」了——
换一批专家（比如换成机票/天气主题），改动量 = 重写 experts.py 一张表。

对照物：fin_triage_example/main.py（单文件版，所有东西挤在一起）
"""

import asyncio
import re
import shutil

from config import MODEL, SERVER_PARAMS  # ⚠️ 必须第一行：先执行 config.py 完成环境变量与 SDK 全局设置
from experts import EXPERTS

from agents import (  # noqa: E402
    Agent,
    HandoffOutputItem,
    RunContextWrapper,
    Runner,
    ToolCallItem,
)
from agents.mcp import MCPServer, MCPServerStdio, ToolFilterContext  # noqa: E402


# ---------- 1. 动态 tool_filter：按「当前是谁在要工具」切工具面 ----------


def route_tools(ctx: ToolFilterContext, tool) -> bool:
    """同一个 server，不同 Agent 看到不同的工具子集。

    签名来自 agents.mcp.util.ToolFilterCallable:
        (ctx: ToolFilterContext, tool: MCPTool) -> bool
    其中 ctx.agent 就是「当前正在请求工具列表的那个 Agent」。
    """
    prefixes = EXPERTS.get(ctx.agent.name, {}).get("prefixes", ())
    return any(tool.name.startswith(p) for p in prefixes)  # type: ignore[union-attr]


# ---------- 2. 构建「前台 + N 专家」 ----------


def build_triage(server: MCPServer) -> Agent:
    experts = [
        Agent(
            name=name,
            model=MODEL,
            handoff_description=str(spec["handoff_description"]),
            instructions=str(spec["instructions"]),
            mcp_servers=[server],  # ← N 个专家共享同一个 server 实例
        )
        for name, spec in EXPERTS.items()
    ]

    return Agent(
        name="Triage Agent",
        model=MODEL,
        instructions=(
            "你是前台分流器。判断用户的行情/宏观问题属于哪一类，转交给对应专家：\n"
            + "\n".join(
                f"  - {spec['handoff_description']}→ {name}"
                for name, spec in EXPERTS.items()
            )
            + "\n只做转交，不要自己回答具体数字。"
        ),
        handoffs=experts,  # ← 只挂 handoffs，不挂 mcp_servers
    )


# ---------- 3. DSML 兜底清洗 + 轨迹提取 ----------

_DSML = re.compile(r"<｜｜DSML｜｜.*?(?:$|>|$)", re.S)


def clean(text: object, last_agent: str) -> str:
    out = _DSML.sub("", str(text or "")).strip()
    return out or f"(模型把调用标记泄漏进了正文，已拦截；实际已由 {last_agent} 接手完成)"


def trace_of(result) -> tuple[list[str], list[str]]:
    hops: list[str] = []
    tools: list[str] = []
    for item in result.new_items:
        if isinstance(item, HandoffOutputItem):
            hops.append(f"{item.source_agent.name} → {item.target_agent.name}")
        elif isinstance(item, ToolCallItem):
            tools.append(str(getattr(item.raw_item, "name", "?")))
    return hops, tools


# ---------- 4. 运行 ----------

QUESTIONS = [
    "现在美元兑人民币的汇率是多少？",            # 期望 → FX Analyst
    "比特币（BTC）现在多少钱？",                  # 期望 → Crypto Analyst
    "纳斯达克指数现在多少点？顺便看下黄金和原油价格。",  # 期望 → Market Analyst
    "中国最新的 GDP 增速是多少？",                # 期望 → Macro Analyst
]


async def main():
    if not shutil.which("npx"):
        raise RuntimeError("npx 未安装，请先安装 Node.js")

    async with MCPServerStdio(
        name="Fin Data MCP",
        cache_tools_list=True,          # 工具列表只拉一次
        tool_filter=route_tools,        # ← 动态过滤，按 Agent 切工具面
        client_session_timeout_seconds=120,  # 首次 npx 构建会慢
        params=SERVER_PARAMS,
    ) as server:
        triage = build_triage(server)

        # 启动自检：证明 filter 生效 —— 每个专家各自能看到几个工具
        # 注意：动态 filter 要求同时传 run_context 和 agent，否则 SDK 会直接报错
        print("=" * 64)
        print("工具面自检（同一个 server 实例，按 Agent 过滤）")
        ctx = RunContextWrapper(context=None)
        saved_filter = server.tool_filter
        server.tool_filter = None  # 临时摘掉 filter，量一下「不过滤」的总数
        print(f"  {'(不过滤)':<18} {len(await server.list_tools())} 个")
        server.tool_filter = saved_filter
        for agent in triage.handoffs:
            visible = await server.list_tools(run_context=ctx, agent=agent)
            print(f"  {agent.name:<18} {len(visible)} 个")
        print("=" * 64)

        for q in QUESTIONS:
            result = await Runner.run(triage, q)
            hops, tools = trace_of(result)
            print("\n" + "-" * 64)
            print(f"Q: {q}")
            print(f"路由: {' | '.join(hops) if hops else '(未发生 handoff)'}")
            print(f"工具: {', '.join(tools) if tools else '(未调用工具)'}")
            print(f"接手: {result.last_agent.name}")
            print(f"A: {clean(result.final_output, result.last_agent.name)}")


if __name__ == "__main__":
    asyncio.run(main())
