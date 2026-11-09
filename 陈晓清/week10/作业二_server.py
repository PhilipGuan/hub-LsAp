from datetime import datetime
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
def get_weather(city: str, date: datetime) -> str:
    """Get weather information for a given city.
    
    Args:
        city: Name of the city.
        date: Date to query weather for (ISO 8601 format, e.g. "2024-06-01").
    """
    return f"Weather in {city} on {date.date()}: Sunny, 25°C"

@mcp.tool
def add_numbers(a: float, b: float) -> float:
    """Add two numbers and return the result."""
    return a + b


@mcp.tool
def multiply_numbers(a: float, b: float) -> float:
    """Multiply two numbers and return the result."""
    return a * b


if __name__ == "__main__":
    mcp.run(transport="sse", port=8000)