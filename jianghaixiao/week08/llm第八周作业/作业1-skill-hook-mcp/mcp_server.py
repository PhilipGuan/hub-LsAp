from mcp.server.mcpserver import MCPServer

mcp = MCPServer(
    name="homework-mcp",
    instructions="提供问候和加法两个工具。",
    version="1.0.0",
)


@mcp.tool(
    title="问候",
    description="根据名字生成一句问候。",
)
def greet(name: str) -> str:
    """向指定的人问好。"""
    return f"你好，{name}！很高兴为你服务。"


@mcp.tool(
    title="加法",
    description="计算两个整数之和。",
)
def add(a: int, b: int) -> int:
    """返回 a + b。"""
    return a + b


if __name__ == "__main__":
    # MCP 端点：http://127.0.0.1:8010/mcp
    mcp.run(transport="streamable-http", host="127.0.0.1", port=8010)
