import random
from fastmcp import FastMCP
from typing import Union
from datetime import datetime

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
def get_weather(city: str, date: str = "") -> str:
    """查询指定城市在指定日期的天气，date 格式 YYYY-MM-DD，不传默认今天。"""
    data = {
        ("北京", "2026-10-15"): "晴，15~25°C，南风 2 级",
        ("上海", "2026-10-15"): "多云，18~23°C",
        ("深圳", "2026-10-15"): "雷阵雨，26~32°C",
    }
    return data.get((city, date), f"{city} 晴，20~28°C")


@mcp.tool
def calculate(expression: str) -> str:
    """计算数学表达式，支持 + - * / 和括号，例如 (3+5)*2。"""
    return str(eval(expression, {"__builtins__": {}}, {}))


@mcp.tool
def get_current_time() -> str:
    """获取当前时间，返回格式 YYYY-MM-DD HH:MM:SS。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


if __name__ == "__main__":
    mcp.run(transport="sse", port=8000)