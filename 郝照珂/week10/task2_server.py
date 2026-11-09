"""基于课程 server.py 扩展：两个原有工具 + 三个新增 MCP 工具。"""
import argparse
import math
import random
import re
from typing import Literal

from fastmcp import FastMCP

mcp = FastMCP(name='Week10 Tools')


@mcp.tool
def roll_dice(n_dice: int) -> list[int]:
    """掷 n_dice 个六面骰子，返回各个点数，数量范围为 1 到 100。"""
    if not 1 <= n_dice <= 100:
        raise ValueError('骰子数量必须在 1 到 100 之间')
    return [random.randint(1, 6) for _ in range(n_dice)]


@mcp.tool
def get_news() -> str:
    """返回课程示例消息（静态演示数据，不是实时新闻）。"""
    return '课程示例消息：正在学习使用 OpenAI Agents SDK 调用 MCP 工具。'


# 以下是本次新增的三个工具。
@mcp.tool
def convert_temperature(value: float, from_unit: Literal['C', 'F'], to_unit: Literal['C', 'F']) -> dict:
    """摄氏/华氏温度换算；C 表示摄氏，F 表示华氏。"""
    if not math.isfinite(value):
        raise ValueError('温度必须为有限数值')
    celsius = value if from_unit == 'C' else (value - 32) * 5 / 9
    if celsius < -273.15:
        raise ValueError('温度不能低于绝对零度')
    result = celsius if to_unit == 'C' else celsius * 9 / 5 + 32
    return {'input': value, 'from_unit': from_unit, 'to_unit': to_unit, 'result': round(result, 4)}


@mcp.tool
def calculate_bmi(weight_kg: float, height_cm: float) -> dict:
    """输入体重（千克）和身高（厘米），计算 BMI 数值，不提供健康诊断。"""
    if not all(math.isfinite(v) and v > 0 for v in (weight_kg, height_cm)):
        raise ValueError('身高和体重必须为正的有限数值')
    return {'weight_kg': weight_kg, 'height_cm': height_cm,
            'bmi': round(weight_kg / (height_cm / 100) ** 2, 2)}


@mcp.tool
def text_statistics(text: str) -> dict:
    """统计总字符数（含空白）、非空白字符数、汉字数、英文单词数和行数；空文本为0行。"""
    return {
        'characters': len(text),
        'non_whitespace_characters': sum(not char.isspace() for char in text),
        'chinese_characters': len(re.findall(r'[\u3400-\u4dbf\u4e00-\u9fff\U00020000-\U0002EBEF]', text)),
        'english_words': len(re.findall(r"[A-Za-z]+(?:['’-][A-Za-z]+)*", text)),
        'lines': len(text.splitlines()),
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--transport', choices=['stdio', 'sse'], default='stdio')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    if args.transport == 'sse':
        mcp.run(transport='sse', host='127.0.0.1', port=args.port)
    else:
        mcp.run(transport='stdio')
