import random
from fastmcp import FastMCP
from typing import Union
import datetime
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
def get_weather(city:str) -> Union[str]:
    """Get weather"""
    return f"{city}的天气晴朗"


@mcp.tool
def get_current_time() -> str:
    """Get the current date and time."""
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

@mcp.tool
def add_numbers(a: float, b: float) -> float:
    """Add two numbers and return the sum.

    Args:
        a: The first number.
        b: The second number.
    """
    return a + b

@mcp.tool
def multiply_numbers(a: float, b: float) -> float:
    """Multiply two numbers and return the product.

    Args:
        a: The first number.
        b: The second number.
    """
    return a * b

if __name__ == "__main__":
    mcp.run(transport="sse", port=8000)