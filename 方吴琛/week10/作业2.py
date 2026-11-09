import os
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field
from fastmcp import FastMCP
from agents import Agent, Runner, AsyncOpenAI, OpenAIChatCompletionsModel
from agents.mcp import MCPServer
from agents.mcp.server import MCPServerSse
from agents import set_default_openai_api, set_tracing_disabled

set_default_openai_api("chat_completions")  # 强制 Chat Completions API（阿里云 dashscope 兼容）
set_tracing_disabled(True)                  # 关闭 SDK 链路追踪


# ============================================================
# 第一部分：定义 MCP Server
# ============================================================
mcp = FastMCP(name="Homework Tools Server")


@mcp.tool
def get_weather(city: str) -> str:
    """查询指定城市的天气情况（模拟数据）。参数 city 为城市中文名。"""
    # 演示用：返回一条模拟天气信息，实际可替换为真实天气 API 调用
    return f"{city}今天晴，气温 22~28℃，空气质量优，适合出行。"


@mcp.tool
def calculator(a: float, b: float, operator: str) -> float:
    """对两个数 a、b 做四则运算。operator 取值：+ - * /。"""
    if operator == "+":
        return a + b
    if operator == "-":
        return a - b
    if operator == "*":
        return a * b
    if operator == "/":
        if b == 0:
            raise ValueError("除数不能为 0")
        return a / b
    raise ValueError(f"不支持的运算符: {operator}")


# 城市名 -> IANA 时区 映射表
CITY_TIMEZONE = {
    "北京": "Asia/Shanghai",
    "上海": "Asia/Shanghai",
    "纽约": "America/New_York",
    "伦敦": "Europe/London",
    "东京": "Asia/Tokyo",
    "巴黎": "Europe/Paris",
    "洛杉矶": "America/Los_Angeles",
    "悉尼": "Australia/Sydney",
}


class WorldClockQuery(BaseModel):
    """世界时钟查询参数（结构化输入，FastMCP 会自动转成 tool 的 JSON Schema）"""
    cities: list[str] = Field(..., description="要查询的城市列表，例如 ['北京', '纽约', '伦敦']")
    reference_city: str = Field("北京", description="作为参照的城市，用于计算时差")
    with_utc_offset: bool = Field(True, description="是否返回各城市的 UTC 偏移量")


@mcp.tool
def world_clock(query: WorldClockQuery) -> dict:
    """世界时钟：查询多个城市的当前时间、UTC 偏移量，以及与参照城市的时差。

    参数 query 为结构化对象，包含 cities（城市列表）、reference_city（参照城市）、
    with_utc_offset（是否返回 UTC 偏移）。
    """
    result: dict = {"reference_city": query.reference_city, "cities": []}

    # 参照城市的时间与 UTC 偏移
    ref_tz = CITY_TIMEZONE.get(query.reference_city, "Asia/Shanghai")
    ref_now = datetime.now(ZoneInfo(ref_tz))
    ref_offset = ref_now.utcoffset().total_seconds()

    for city in query.cities:
        # 城市名映射到 IANA 时区；不在映射表中时，直接把输入当作时区名兜底
        tz_name = CITY_TIMEZONE.get(city, city)
        try:
            now = datetime.now(ZoneInfo(tz_name))
        except Exception:
            result["cities"].append({"city": city, "error": f"无法识别的时区: {tz_name}"})
            continue

        entry = {
            "city": city,
            "timezone": tz_name,
            "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
            "is_dst": now.dst().total_seconds() != 0,  # 是否夏令时
            "time_diff_hours": round((now.utcoffset().total_seconds() - ref_offset) / 3600, 1),
        }
        if query.with_utc_offset:
            entry["utc_offset_hours"] = round(now.utcoffset().total_seconds() / 3600, 1)
        result["cities"].append(entry)

    return result


# ============================================================
# 第二部分：定义 OpenAI Agent 并调用 MCP 工具
# ============================================================
async def run(mcp_server: MCPServer):
    external_client = AsyncOpenAI(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.environ["OPENAI_BASE_URL"],
    )

    agent = Agent(
        name="Assistant",
        instructions="你是一个助手，可以调用 MCP 工具来回答用户问题。",
        mcp_servers=[mcp_server],  # 把 MCP 工具注册给 agent
        model=OpenAIChatCompletionsModel(
            model="qwen-max",
            openai_client=external_client,
        ),
    )

    # 大模型自动完成：工具选择 -> 参数填充 -> 调用工具 -> 汇总结果
    queries = [
        "帮我查一下深圳今天天气怎么样？",        # 触发 get_weather
        "帮我算一下 12.5 乘以 8 等于多少？",     # 触发 calculator
        "帮我看看北京、纽约、伦敦现在分别是几点，以北京为参照算一下时差。",  # 触发 world_clock
    ]
    for message in queries:
        print(f"\nRunning: {message}")
        result = await Runner.run(starting_agent=agent, input=message)
        print("结果:", result.final_output)


async def main():
    # 后台启动 MCP SSE 服务（相当于参考里的 server.py）
    server_task = asyncio.create_task(mcp.run_async(transport="sse", port=8000))
    await asyncio.sleep(3)  # 等待服务就绪

    try:
        # 客户端通过 SSE 方式连接远程 MCP server（相当于参考里的 client.py）
        async with MCPServerSse(
            name="SSE Python Server",
            params={
                "url": "http://localhost:8000/sse",  # mcp server 通过 http 方式沟通
            },
        ) as server:
            await run(server)
    finally:
        server_task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
