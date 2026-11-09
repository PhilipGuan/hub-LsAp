"""
作业2：在 2-兼容OpenAI-Agent 的基础上，自己增加 3 个 MCP 工具，并通过 Agent 调用。

参考代码：
    Week10/2-fastmcp使用/2-兼容OpenAI-Agent/server.py   原有的 2 个工具
    Week10/2-fastmcp使用/2-兼容OpenAI-Agent/client.py   Agent 端

原有工具（保留，用于回归测试）：
    1) roll_dice(n_dice)   摇骰子
    2) get_news()          获取最新消息

新增 3 个工具：
    3) get_weather(city, days)                  查天气   （演示：参数说明 + 取值范围约束 + 默认值）
    4) convert_currency(amount, from, to)       货币换算 （演示：Literal 枚举约束）
    5) calculate(expression)                    四则运算 （演示：自定义工具名 + 参数校验 + 异常兜底）

运行（终端 A，保持不关）：
    python work2_server.py
然后另开一个终端跑客户端：
    python work2_client.py
"""
import random
from typing import Union, Annotated, Literal

from fastmcp import FastMCP
from pydantic import Field

mcp = FastMCP(name="Dice Roller")


# ==================== 原有工具（照搬课程 server.py，用于回归测试） ====================
@mcp.tool
def roll_dice(n_dice: int) -> list[int]:
    """Roll `n_dice` 6-sided dice and return the results."""
    # 课程原代码是 randint(10, 60)，和 docstring 说的 6 面骰子对不上，这里修正为 1~6
    return [random.randint(1, 6) for _ in range(n_dice)]


@mcp.tool
def get_news() -> Union[int, str]:
    """Get latest news."""
    return "最新消息 Qwen4 is coming!"


# ==================== 新增工具 1：查天气 ====================
@mcp.tool
def get_weather(
    city: Annotated[str, Field(description="城市名称，用中文，例如：武汉、北京、上海")],
    days: Annotated[int, Field(description="查询未来几天，取值范围 1~7，不传默认查今天", ge=1, le=7)] = 1,
) -> str:
    """查询指定城市未来几天的天气情况。

    当用户询问天气、气温、会不会下雨、要不要带伞、哪个城市更热时，使用本工具。

    Args:
        city: 中文城市名，例如 武汉
        days: 1~7 的整数，表示查询未来几天，默认 1 天

    Returns:
        每个城市每天一行，包含天气状况和气温（摄氏度）
    """
    # 用城市名当随机种子，保证同一个城市每次查到的结果一致（方便对比、演示）
    rng = random.Random(sum(ord(c) for c in city))
    lines = []
    for i in range(days):
        condition = "晴" if rng.random() > 0.3 else "小雨"
        lines.append(f"{city} 第{i + 1}天：{condition}，气温 {rng.randint(15, 32)}℃")
    return "\n".join(lines)


# ==================== 新增工具 2：货币换算 ====================
@mcp.tool
def convert_currency(
    amount: Annotated[float, Field(description="要换算的金额，例如 100")],
    from_currency: Annotated[Literal["CNY", "USD", "EUR", "JPY"], Field(description="原币种代码")],
    to_currency: Annotated[Literal["CNY", "USD", "EUR", "JPY"], Field(description="目标币种代码")],
) -> str:
    """把一笔钱从一个币种换算成另一个币种。

    当用户问 100 美元等于多少人民币、美元汇率是多少 这类问题时，使用本工具。

    Args:
        amount: 金额，例如 100
        from_currency: 原币种代码，只能是 CNY / USD / EUR / JPY
        to_currency: 目标币种代码，只能是 CNY / USD / EUR / JPY

    Returns:
        换算结果，例如 100 USD = 720.00 CNY
    """
    # 示例汇率（以人民币为基准），真实项目里应该去调汇率接口
    rate_to_cny = {"CNY": 1.0, "USD": 7.2, "EUR": 7.8, "JPY": 0.048}
    try:
        result = amount * rate_to_cny[from_currency] / rate_to_cny[to_currency]
    except (KeyError, ZeroDivisionError) as e:
        return f"换算失败：{e}"
    return f"{amount} {from_currency} = {result:.2f} {to_currency}"


# ==================== 新增工具 3：四则运算 ====================
@mcp.tool(
    name="calculate",
    description="计算一个数学表达式的结果。当用户需要做加减乘除、算术计算时使用。",
)
def calculate(
    expression: Annotated[str, Field(description="只包含数字和 + - * / ( ) 的表达式，例如：(3+5)*2")],
) -> str:
    """计算四则运算表达式的结果。

    Args:
        expression: 只允许数字、小数点、和 + - * / ( ) 符号，例如 (3+5)*2

    Returns:
        计算过程与结果，例如 (3+5)*2 = 16；表达式非法时返回错误提示
    """
    allowed = set("0123456789+-*/() .")
    if not expression or not set(expression) <= allowed:
        return "表达式不合法：只允许数字和 + - * / ( ) 以及小数点"
    try:
        # 演示用：先白名单过滤字符再 eval。生产环境请改用 ast 安全求值，禁止直接 eval
        return f"{expression} = {eval(expression)}"
    except Exception as e:
        return f"计算失败：{e}"


if __name__ == "__main__":
    # 和课程示例保持一致：SSE 传输 + 8000 端口
    # 对应的客户端地址是 http://localhost:8000/sse
    mcp.run(transport="sse", port=8000)