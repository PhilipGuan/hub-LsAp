import random
from datetime import datetime
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


# ============ 新增的 3 个工具 ============

@mcp.tool
def add_numbers(a: float, b: float) -> float:
    """Add two numbers and return their sum."""
    return a + b


@mcp.tool
def get_weather(city: str) -> str:
    """Get the current weather of a given city."""
    weather_data = {
        "北京": "晴，25°C，空气质量优",
        "上海": "多云，28°C，湿度较高",
        "广州": "雷阵雨，30°C，注意携带雨具",
        "深圳": "阵雨，29°C，东南风3级",
        "杭州": "晴转多云，26°C，适合出行",
    }
    return weather_data.get(city, f"{city}：晴，26°C（模拟数据）")


@mcp.tool
def get_current_time() -> str:
    """Get the current date and time."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


if __name__ == "__main__":
    mcp.run(transport="sse", port=8000)