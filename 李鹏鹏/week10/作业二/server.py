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


# ============ 新增的 3 个 MCP 工具 ============

@mcp.tool
def get_current_time() -> str:
    """获取当前的日期和时间，返回格式化的时间字符串。"""
    now = datetime.datetime.now()
    return now.strftime("%Y-%m-%d %H:%M:%S")


@mcp.tool
def calculate_bmi(weight_kg: float, height_m: float) -> dict:
    """根据体重（公斤）和身高（米）计算 BMI 指数，并返回健康建议。"""
    if weight_kg <= 0 or height_m <= 0:
        return {"error": "体重和身高必须为正数"}
    bmi = round(weight_kg / (height_m ** 2), 2)
    if bmi < 18.5:
        advice = "偏瘦，建议适当增加营养摄入。"
    elif bmi < 24:
        advice = "正常，请继续保持健康的生活方式。"
    elif bmi < 28:
        advice = "偏胖，建议控制饮食并加强锻炼。"
    else:
        advice = "肥胖，建议咨询医生制定减重计划。"
    return {"bmi": bmi, "advice": advice}


@mcp.tool
def get_weather(city: str) -> dict:
    """查询指定城市的天气（模拟数据，返回天气状况和温度）。"""
    weather_types = ["晴", "多云", "阴", "小雨", "雷阵雨"]
    weather = random.choice(weather_types)
    temperature = random.randint(15, 35)
    return {"city": city, "weather": weather, "temperature": f"{temperature}℃"}


if __name__ == "__main__":
    mcp.run(transport="sse", port=8000)