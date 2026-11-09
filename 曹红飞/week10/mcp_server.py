import random
from fastmcp import FastMCP
from typing import Union
mcp = FastMCP(name="Dice Roller")

# 工具名字、传入参数类型、返回值类型、函数的注释（工具说明）

@mcp.tool
def roll_dice(n_dice: int) -> list[int]:
    """Roll `n_dice` 6-sided dice and return the results."""
    return [random.randint(10, 60) for _ in range(n_dice)]

@mcp.tool
def get_news() -> Union[int, str]:
    """Get latest news."""
    return "最新消息 Qwen4 is coming!"

@mcp.tool
def add(a: int, b: int) -> int:
    """Add two numbers"""
    return a + b

@mcp.tool
def greet(name: str) -> str:
    """Say hello to someone"""
    return f"Hello, I'm MCP Server and I'm glad to meet you, {name}!"

@mcp.tool
def get_city() -> str:
    """get which city you are from"""
    return "guangzhou"


if __name__ == "__main__":
    mcp.run(transport="sse", port=8000)
