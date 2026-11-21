"""后端进程3：接收进程2的识别结果，使用 LangGraph 接入 DeepSeek 处理：正向情感鼓励，负面情感排解。"""
import os
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel
from typing_extensions import TypedDict

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

if not os.environ.get("DEEPSEEK_API_KEY"):
    raise RuntimeError("未配置 DEEPSEEK_API_KEY，请在 src/backend/.env 中设置")

llm = ChatOpenAI(
    model="deepseek-chat",
    api_key=os.environ["DEEPSEEK_API_KEY"],
    base_url="https://api.deepseek.com",
    temperature=0.7,
)


class State(TypedDict):
    emotion: str
    polarity: str
    summary: str
    response: str


def route(state: State) -> str:
    """正向情感进入鼓励节点，其余进入排解节点。"""
    return "encourage" if state["polarity"] == "positive" else "console"


def encourage(state: State) -> dict:
    prompt = (
        f"用户当前的情感是「{state['emotion']}」（{state['summary']}），属于正向情感。"
        "请用温暖真诚的语气给用户一句鼓励，不超过50字。"
    )
    msg = llm.invoke([HumanMessage(content=prompt)])
    return {"response": msg.content}


def console(state: State) -> dict:
    prompt = (
        f"用户当前的情感是「{state['emotion']}」（{state['summary']}），属于负向或中性情感。"
        "请用温柔共情的语气安抚、排解用户的情绪，不超过80字。"
    )
    msg = llm.invoke([HumanMessage(content=prompt)])
    return {"response": msg.content}


builder = StateGraph(State)
builder.add_node("encourage", encourage)
builder.add_node("console", console)
builder.add_conditional_edges(START, route)
builder.add_edge("encourage", END)
builder.add_edge("console", END)
graph = builder.compile()

app = FastAPI(title="process3_consolation")


class Recognition(BaseModel):
    emotion: str = ""
    polarity: str = "neutral"
    summary: str = ""


@app.post("/process")
async def process(rec: Recognition):
    result = graph.invoke(
        {"emotion": rec.emotion, "polarity": rec.polarity, "summary": rec.summary, "response": ""}
    )
    return {"response": result["response"]}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8003)
