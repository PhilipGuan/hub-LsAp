import ast
import operator as op
import random
from datetime import datetime
from typing import Union
from zoneinfo import ZoneInfo

from fastmcp import FastMCP

mcp = FastMCP(name="Dice Roller")

# ============================================================
# 课程原有工具
# ============================================================
@mcp.tool
def roll_dice(n_dice: int) -> list[int]:
    """Roll `n_dice` 6-sided dice and return the results."""
    return [random.randint(10, 60) for _ in range(n_dice)]


@mcp.tool
def get_news() -> Union[int, str]:
    """Get latest news."""
    return "最新消息 Qwen4 is coming!"


# ============================================================
# 作业2：自己新增的 3 个 MCP 工具
# ============================================================

# ---- 工具1：查询指定时区的当前时间 ----
@mcp.tool
def get_current_time(timezone: str = "Asia/Shanghai") -> str:
    """Get the current date and time for a given IANA timezone, e.g. 'Asia/Shanghai', 'UTC', 'America/New_York'."""
    return datetime.now(ZoneInfo(timezone)).strftime("%Y-%m-%d %H:%M:%S %Z")


# ---- 工具2：查询城市天气（mock 数据）----
_WEATHER = {
    "beijing": ("晴", 24),
    "shanghai": ("多云", 26),
    "shenzhen": ("小雨", 29),
    "hangzhou": ("晴", 25),
    "guangzhou": ("雷阵雨", 30),
}
_CITY_ALIASES = {"北京": "beijing", "上海": "shanghai", "深圳": "shenzhen", "杭州": "hangzhou", "广州": "guangzhou"}


@mcp.tool
def get_weather(city: str) -> dict:
    """Get the current weather of a city. Returns the condition and temperature in Celsius."""
    key = _CITY_ALIASES.get(city.strip(), city.strip().lower())
    condition, temp = _WEATHER.get(key, ("未知", 25))
    return {"city": city, "condition": condition, "temperature_c": temp}


# ---- 工具3：安全地计算四则运算表达式 ----
_BIN_OPS = {
    ast.Add: op.add,
    ast.Sub: op.sub,
    ast.Mult: op.mul,
    ast.Div: op.truediv,
    ast.FloorDiv: op.floordiv,
    ast.Mod: op.mod,
    ast.Pow: op.pow,
}
_UNARY_OPS = {ast.UAdd: op.pos, ast.USub: op.neg}


def _eval_node(node: ast.AST):
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        return _BIN_OPS[type(node.op)](_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_eval_node(node.operand))
    raise ValueError("不支持的表达式，仅支持数字与 + - * / // % ** 及括号")


@mcp.tool
def calculate(expression: str) -> float:
    """Evaluate a basic arithmetic expression, e.g. '(3 + 4) * 2' or '10 / 4'. Supports + - * / // % ** and parentheses."""
    return _eval_node(ast.parse(expression, mode="eval"))


if __name__ == "__main__":
    mcp.run(transport="sse", port=8000)
