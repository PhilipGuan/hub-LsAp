import random
from fastmcp import FastMCP
from typing import Annotated, Union
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
def calculator(
    operation: Annotated[str, "Arithmetic operation, support: add, subtract, multiply, divide"],
    a: Annotated[float, "First operand"],
    b: Annotated[float, "Second operand"]
) -> str:
    """
    A simple calculator supporting four arithmetic operations: add, subtract, multiply, divide.
    operation: add -> a + b
    operation: subtract -> a - b
    operation: multiply -> a * b
    operation: divide -> a / b, will throw error if b is zero
    """
    match operation:
        case "add":
            return f"计算结果为：{a + b}"
        case "subtract":
            return f"计算结果为：{a - b}"
        case "multiply":
            return f"计算结果为：{a * b}"
        case "divide":
            if b == 0:
                raise ValueError("Cannot divide by zero")
            return f"计算结果为：{a / b}"
        case _:
            raise ValueError(f"Unsupported operation: {operation}. Valid: add, subtract, multiply, divide")

@mcp.tool()
def str_length(
    content: Annotated[str, "Input string to calculate length"]
) -> str:
    """Calculate the length of input string, return character count."""
    return f"输入的字符串长度为：{len(content)}"


@mcp.tool()
def is_numeric_string(
    content: Annotated[str, "Input string to check whether it is pure numeric"]
) -> str:
    """Judge if input string is pure numeric. Support positive/negative number and decimal point."""
    try:
        float(content)
        return "输入的字符串为纯数值"
    except ValueError:
        return "输入的字符串为非纯数值"

if __name__ == "__main__":
    mcp.run(transport="sse", port=8000)