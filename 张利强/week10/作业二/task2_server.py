import random
from fastmcp import FastMCP
from typing import Union
from datetime import datetime, timezone, timedelta

mcp = FastMCP(name="Dice Roller")

@mcp.tool
def roll_dice(n_dice: int) -> list[int]:
    """Roll `n_dice` 6-sided dice and return the results."""
    return [random.randint(10, 60) for _ in range(n_dice)]

@mcp.tool
def get_news() -> str:
    """Get latest news."""
    return "最新消息 Qwen4 is coming!"

# ============ 以下为新增的三个工具 ============

@mcp.tool
def calculator(expression: str) -> Union[float, str]:
    """计算一个数学表达式并返回结果（内部使用 eval 求值）。

    Args:
        expression: 数学表达式字符串，例如 "2 + 3 * 4"、"(1+2)**3"、"10 / 4"。

    Returns:
        计算结果（float）；若表达式非法则返回错误信息字符串。
    """
    # 出于安全考虑，仅允许数字、运算符、括号和空白字符
    allowed = set("0123456789+-*/().% ")
    if not set(expression) <= allowed:
        return "错误：表达式包含不支持的字符"

    try:
        # 禁用内置函数，降低 eval 风险
        result = eval(expression, {"__builtins__": {}}, {})
        return float(result)
    except ZeroDivisionError:
        return "错误：除数不能为 0"
    except Exception as e:
        return f"计算失败：{e}"


@mcp.tool
def tokenize(text: str, mode: str = "precise", top_k: int = 0) -> dict:
    """使用 jieba 对中文文本进行分词。

    Args:
        text: 待分词的文本。
        mode: 分词模式，"precise"（精确模式）、"full"（全模式）、
              "search"（搜索引擎模式），默认 "precise"。
        top_k: 若大于 0，则额外返回出现频率最高的前 top_k 个词。

    Returns:
        包含分词结果、词数和（可选）高频词的字典。
    """
    try:
        import jieba
    except ImportError:
        return {"error": "未安装 jieba，请先执行 pip install jieba"}

    if mode == "full":
        tokens = list(jieba.cut(text, cut_all=True))
    elif mode == "search":
        tokens = list(jieba.cut_for_search(text))
    else:  # precise
        tokens = list(jieba.cut(text))

    # 过滤空白 token
    tokens = [t for t in tokens if t.strip()]

    result = {"tokens": tokens, "count": len(tokens), "mode": mode}

    if top_k > 0:
        freq: dict[str, int] = {}
        for t in tokens:
            freq[t] = freq.get(t, 0) + 1
        result["top_words"] = sorted(freq.items(), key=lambda x: -x[1])[:top_k]

    return result


@mcp.tool
def get_time(tz_offset: int = 8, fmt: str = "%Y-%m-%d %H:%M:%S") -> dict:
    """获取当前时间。

    Args:
        tz_offset: 时区偏移（小时），默认 8（东八区，北京时间）。
                   例如 0 表示 UTC，-5 表示西五区。
        fmt: 时间格式化字符串，默认 "%Y-%m-%d %H:%M:%S"。

    Returns:
        包含格式化时间、时间戳、时区偏移和星期几的字典。
    """
    try:
        tz = timezone(timedelta(hours=tz_offset))
        now = datetime.now(tz)
    except Exception as e:
        return {"error": f"参数错误：{e}"}

    weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    return {
        "datetime": now.strftime(fmt),
        "timestamp": now.timestamp(),
        "tz_offset": tz_offset,
        "weekday": weekdays[now.weekday()],
        "iso": now.isoformat(),
    }

# 启动： fastmcp run task2_server.py --transport http --port 8000
if __name__ == "__main__":
    mcp.run(transport="http", port=8000)

"""
mcp适合定义轻量、查询类型的工具
不适合复杂、写入/删除类型的工具
"""