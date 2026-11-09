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


# ===== 作业2：新增的 3 个 MCP 工具 =====

@mcp.tool
def get_current_date() -> str:
    """获取今天的日期和星期。当用户询问今天几号、星期几、当前日期时使用。"""
    now = datetime.now()
    return f"{now:%Y-%m-%d} 星期{'一二三四五六日'[now.weekday()]}"


@mcp.tool
def get_weather(city: str, unit: str = "摄氏度") -> str:
    """查询指定城市的天气。当用户询问某地天气、气温、出门要不要带伞时使用。"""
    seed = sum(ord(c) for c in city)
    cond = ("晴", "多云", "小雨", "阴")[seed % 4]
    temp = 12 + seed % 18
    if unit.strip().lower() in ("华氏度", "f", "fahrenheit"):
        return f"{city}：{cond}，{temp * 9 // 5 + 32}°F"
    return f"{city}：{cond}，{temp}°C"


@mcp.tool
def search_notes(keyword: str, limit: int = 3) -> list[dict]:
    """按关键词检索课程笔记，返回匹配的笔记列表。当用户想查找、搜索课程笔记或资料时使用。"""
    notes = [
        {"week": "第06周", "title": "RAG 工程化实现", "tags": ["rag", "检索", "向量库"],
         "summary": "文档切分、向量化、召回与重排的工程落地。"},
        {"week": "第07周", "title": "RAG 进阶与改进", "tags": ["rag", "rerank", "评估"],
         "summary": "混合检索、重排模型与 RAG 效果评估方法。"},
        {"week": "第08周", "title": "Agent 开发与 VibeCoding 基础", "tags": ["agent", "vibecoding"],
         "summary": "用自然语言驱动编码，Agent 工作流搭建。"},
        {"week": "第09周", "title": "低代码 Agent - Dify 智能体搭建", "tags": ["agent", "dify", "低代码"],
         "summary": "Dify 工作流编排与智能体发布。"},
        {"week": "第10周", "title": "Agent 搭建与 MCP 应用", "tags": ["agent", "mcp", "fastmcp"],
         "summary": "OpenAI Agents SDK 接入 FastMCP 服务端。"},
    ]
    k = keyword.strip().lower()
    hits = [n for n in notes if k in (n["title"] + n["summary"] + " ".join(n["tags"])).lower()]
    return hits[: max(1, limit)]


if __name__ == "__main__":
    mcp.run(transport="sse", port=8000)
