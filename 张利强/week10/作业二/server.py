import random
from fastmcp import FastMCP
from pydantic import BaseModel
import requests
from typing import Annotated

TOKEN = "7e379d7f3330"

mcp = FastMCP(name="Dice Roller")

# 工具名字、传入参数类型、返回值类型、函数的注释（工具说明）

@mcp.tool
def roll_dice(n_dice: int) -> list[int]:
    """Roll `n_dice` 6-sided dice and return the results."""
    return [random.randint(10, 60) for _ in range(n_dice)]

@mcp.tool
def get_news() -> str:
    """Get latest news."""
    return "最新消息 Qwen4 is coming!"

class CalendarEventResult(BaseModel):
    event_id: str
    title: str
    start_date: str
    end_date: str
    status: str

@mcp.tool
def createCalendarEvent(
        title: str,
        startDate: str,
        endDate: str,
) -> CalendarEventResult:
    """Create a calendar event.

    Args:
        title: Event title
        startDate: Start date, format YYYY-MM-DD
        endDate: End date, format YYYY-MM-DD
    """
    return CalendarEventResult(
        event_id="12345",
        title=title,
        start_date=startDate,
        end_date=endDate,
        status="confirmed",
    )

@mcp.tool
def sendEmail(
        to: str,
        subject: str,
        body: str,
) -> dict:
    """Send an email.

    Args:
        to: Recipient email address
        subject: Email subject
        body: Email body
    """
    return {"status": "success",
            "to": to,
            "subject": subject,
            "body": body}

# 手机号归属地+运营商查询
@mcp.tool
def get_tel_info(tel_no: Annotated[str, "Tel phone number"]):
    """Retrieves basic information (location, carrier) for a given telephone number."""
    try:
        return requests.get(f"https://whyta.cn/api/tx/mobilelocal?key={TOKEN}&phone={tel_no}").json()["result"]
    except:
        return []

# 启动： fastmcp run server.py --transport sse --port 8900
if __name__ == "__main__":
    mcp.run(transport="sse", port=8900)