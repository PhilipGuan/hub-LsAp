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


# ===== 新增的 3 个工具 =====

@mcp.tool
def add(a: int, b: int) -> int:
    """Add two integers and return their sum."""
    return a + b


@mcp.tool
def get_weather(city: str) -> str:
    """Get the current weather for a given city (mocked)."""
    weather_map = {
        "北京": "晴，25°C",
        "上海": "多云，28°C",
        "深圳": "晴，30°C",
        "杭州": "小雨，22°C",
    }
    return weather_map.get(city, f"{city}：晴，20°C（模拟数据）")


@mcp.tool
def count_words(text: str) -> int:
    """Count the number of characters in the given text."""
    return len(text)


if __name__ == "__main__":
    mcp.run(transport="sse", port=8000)