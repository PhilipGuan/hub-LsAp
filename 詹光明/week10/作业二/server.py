import random
import datetime
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
def get_weather(city: str) -> dict:
    """查询指定城市的当前天气，返回天气、温度与湿度。"""
    # 用本地模拟数据，避免依赖外部天气 API
    return {
        "city": city,
        "weather": random.choice(["晴", "多云", "阴", "小雨"]),
        "temperature": random.randint(15, 32),
        "humidity": random.randint(30, 90),
        "unit": "摄氏度",
    }


# 数学计算
@mcp.tool
def calculate(expression: str) -> float:
    """计算数学表达式的值"""
    # 白名单校验后再求值，避免 eval 执行任意代码
    allowed_chars = set("0123456789+-*/(). ")
    if not set(expression) <= allowed_chars:
        raise ValueError(f"表达式包含不允许的字符：{expression}")
    return float(eval(expression))


# 获取当前时间
@mcp.tool
def get_current_time() -> str:
    """Get the current date and time. 获取系统当前的日期与时间。"""
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


if __name__ == "__main__":
    mcp.run(transport="sse", port=8000)