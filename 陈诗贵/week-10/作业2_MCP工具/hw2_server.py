# -*- coding: utf-8 -*-
"""作业2 MCP Server：在 2-兼容OpenAI-Agent/server.py 基础上新增 3 个工具。

原有工具：roll_dice、get_news
新增工具：get_weather（模拟天气）、calculate（安全计算器）、get_current_time（当前时间）
"""
import ast
import operator
import random
from datetime import datetime
from typing import Union

from fastmcp import FastMCP

mcp = FastMCP(name="Dice Roller")

# ---------------- 原有工具 ----------------


@mcp.tool
def roll_dice(n_dice: int) -> list[int]:
    """Roll `n_dice` 6-sided dice and return the results."""
    return [random.randint(10, 60) for _ in range(n_dice)]


@mcp.tool
def get_news() -> Union[int, str]:
    """Get latest news."""
    return "最新消息 Qwen4 is coming!"


# ---------------- 新增工具 1：模拟天气查询 ----------------

_FAKE_WEATHER = {
    "北京": "晴，25℃，微风",
    "上海": "多云，28℃，东南风3级",
    "深圳": "雷阵雨，30℃，南风2级",
    "杭州": "小雨，24℃，东北风2级",
}


@mcp.tool
def get_weather(city: str) -> str:
    """查询指定城市的天气（模拟数据）。参数 city 为城市中文名，如：北京。"""
    weather = _FAKE_WEATHER.get(city)
    if weather is None:
        return f"暂无「{city}」的天气数据，目前仅支持：{ '、'.join(_FAKE_WEATHER) }"
    return f"{city}今天{weather}"


# ---------------- 新增工具 2：安全计算器 ----------------

_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
}


def _safe_eval(node):
    """递归计算 AST，只允许数字和基本运算符，杜绝任意代码执行。"""
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_safe_eval(node.left), _safe_eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_safe_eval(node.operand))
    raise ValueError("表达式中包含不支持的语法")


@mcp.tool
def calculate(expression: str) -> str:
    """计算数学表达式，支持 + - * / % ** 和括号，例如 (3+5)*2。"""
    try:
        value = _safe_eval(ast.parse(expression, mode="eval"))
        return f"{expression} = {value}"
    except ZeroDivisionError:
        return "计算错误：除数不能为 0"
    except Exception as e:
        return f"无法计算「{expression}」：{e}"


# ---------------- 新增工具 3：当前时间 ----------------


@mcp.tool
def get_current_time() -> str:
    """获取当前的日期和时间。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


if __name__ == "__main__":
    mcp.run(transport="sse", port=8000)
