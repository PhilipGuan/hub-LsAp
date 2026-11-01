"""A/B 实验版：把 fin_triage_v2 的 handoffs 换成 as_tool，用来验证一条推理。

要验证的推理是：
    「as_tool 模式下，专家的答案要经过编排者改写一轮，改写的每一步都可能把数字写错。」

只改了一处接线（config.py / experts.py 与 v2 完全相同，一行未动）：

    fin_triage_v2 : Agent(..., handoffs=experts)                    ← 换人
    本目录        : Agent(..., tools=[e.as_tool(...) for e in experts]) ← 当成工具调用

为了能判定「数字有没有漂」，本版本额外做一件事：
把**子 Agent 的原话**和**编排者的转述**同时打印出来。

    子 Agent 原文  = ToolCallOutputItem.output（工具返回值，就是子 Agent 的 final_output）
    A(编排器转述)  = result.final_output（编排者自己写的那段）

这样就在**同一次运行、同一批数据**上完成对照——数据源完全相同，
唯一变量就是「多出来的那一轮生成」。这比拿两次运行的数字去比要干净得多。

对照物：fin_triage_v2/main.py
"""

import asyncio
import re
import shutil

from config import MODEL, SERVER_PARAMS  # ⚠️ 必须第一行：先执行 config.py 完成环境变量与 SDK 全局设置
from experts import EXPERTS

from agents import (  # noqa: E402
    Agent,
    RunContextWrapper,
    Runner,
    ToolCallItem,
    ToolCallOutputItem,
)
from agents.mcp import MCPServer, MCPServerStdio, ToolFilterContext  # noqa: E402


# ---------- 1. 动态 tool_filter：与 v2 完全一致 ----------


def route_tools(ctx: ToolFilterContext, tool) -> bool:
    """同一个 server，不同 Agent 看到不同的工具子集。"""
    prefixes = EXPERTS.get(ctx.agent.name, {}).get("prefixes", ())
    return any(tool.name.startswith(p) for p in prefixes)  # type: ignore[union-attr]


# ---------- 2. 构建「编排者 + N 个被包装成工具的专家」 ----------


def build_orchestrator(server: MCPServer) -> tuple[Agent, list[Agent]]:
    experts: list[Agent] = []
    agent_tools = []

    for name, spec in EXPERTS.items():
        expert = Agent(
            name=name,
            model=MODEL,
            instructions=str(spec["instructions"]),
            mcp_servers=[server],  # ← 专家仍然共享同一个 server 实例
        )
        experts.append(expert)
        agent_tools.append(
            expert.as_tool(
                # tool_name=None → 用 Agent 名转换而来（FX Analyst → fx_analyst）
                tool_name=None,
                tool_description=str(spec["handoff_description"]),
            )
        )

    orchestrator = Agent(
        name="Triage Agent",
        model=MODEL,
        instructions=(
            "你是行情助手。判断用户的问题属于下面哪一类，然后调用对应工具取得答案：\n"
            + "\n".join(
                f"  - {spec['handoff_description']}→ {name}"
                for name, spec in EXPERTS.items()
            )
            + "\n\n规则：\n"
            "1. 必须调用工具取数，不要凭记忆回答。\n"
            "2. 工具返回的数字必须**原样搬运**进你的回答——可以调整措辞，"
            "但数字、单位、时间点必须与工具返回完全一致，一个字都不许改。\n"
            "3. 一次只调用一个工具。"
        ),
        tools=agent_tools,  # ← 关键差异：这里不是 handoffs
    )
    return orchestrator, experts


# ---------- 3. DSML 兜底清洗 + 轨迹提取 ----------

_DSML = re.compile(r"<｜｜DSML｜｜.*?(?:$|>|$)", re.S)


def clean(text: object, last_agent: str) -> str:
    out = _DSML.sub("", str(text or "")).strip()
    return out or f"(模型把调用标记泄漏进了正文，已拦截；实际已由 {last_agent} 接手完成)"


def trace_of(result) -> tuple[list[str], list[str]]:
    """返回 (调用链, 子 Agent 原话列表)。

    注意与 v2 的差别：这里没有 HandoffOutputItem 可读，只有工具调用记录。
    """
    calls: list[str] = []
    sub_outputs: list[str] = []
    for item in result.new_items:
        if isinstance(item, ToolCallItem):
            calls.append(str(getattr(item.raw_item, "name", "?")))
        elif isinstance(item, ToolCallOutputItem):
            sub_outputs.append(str(item.output))
    return calls, sub_outputs


# ---------- 4. 运行 ----------

QUESTIONS = [
    "现在美元兑人民币的汇率是多少？",            # 期望 → fx_analyst
    "比特币（BTC）现在多少钱？",                  # 期望 → crypto_analyst
    "纳斯达克指数现在多少点？顺便看下黄金和原油价格。",  # 期望 → market_analyst
    "中国最新的 GDP 增速是多少？",                # 期望 → macro_analyst
]


async def main():
    if not shutil.which("npx"):
        raise RuntimeError("npx 未安装，请先安装 Node.js")

    async with MCPServerStdio(
        name="Fin Data MCP",
        cache_tools_list=True,
        tool_filter=route_tools,
        client_session_timeout_seconds=120,
        params=SERVER_PARAMS,
    ) as server:
        orchestrator, experts = build_orchestrator(server)

        # 启动自检：证明 filter 依然生效（与 v2 同一套逻辑，只是遍历 experts 而不是 handoffs）
        print("=" * 64)
        print("工具面自检（同一个 server 实例，按 Agent 过滤）")
        ctx = RunContextWrapper(context=None)
        saved_filter = server.tool_filter
        server.tool_filter = None
        print(f"  {'(不过滤)':<18} {len(await server.list_tools())} 个")
        server.tool_filter = saved_filter
        for agent in experts:
            visible = await server.list_tools(run_context=ctx, agent=agent)
            print(f"  {agent.name:<18} {len(visible)} 个")
        print("=" * 64)
        print("编排者可用的 agent-tool:", ", ".join(t.name for t in orchestrator.tools))

        for q in QUESTIONS:
            result = await Runner.run(orchestrator, q)
            calls, sub_outputs = trace_of(result)

            print("\n" + "=" * 64)
            print(f"Q: {q}")
            print(f"调用链: {' | '.join(calls) if calls else '(未调用任何工具)'}")
            print(f"接手: {result.last_agent.name}   ← 注意：控制权没有交出去")
            print(f"new_items 条数: {len(result.new_items)}")
            print("-" * 64)
            if sub_outputs:
                print("【子 Agent 原文】（工具返回值）")
                print(sub_outputs[-1])
                print("-" * 64)
            print("【A：编排者转述】（最终交给用户的那段）")
            print(clean(result.final_output, result.last_agent.name))


if __name__ == "__main__":
    asyncio.run(main())
