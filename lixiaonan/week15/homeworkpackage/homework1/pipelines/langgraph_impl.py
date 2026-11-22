"""方案 A：LangGraph 实现（设计文档 §4.2）。

图结构（条件边实现按情感标签分流）：

    START ──▶ sentiment ──(route_by_sentiment)──▶ encourage / comfort / neutral ──▶ END

进程1 = sentiment 节点（结构化输出 SentimentResult）；
进程2 = 三个响应节点之一（条件边按 label 路由，各自持有分支提示词）。

LLM 客户端延迟构建：导入本模块不触碰模型（无 Key 也能导入并做纯逻辑测试），
首次调用 run_pipeline 时才构建并校验配置（设计文档 §7）。
"""

import time
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from config import validate_settings
from prompts import LABEL_TO_BRANCH, SENTIMENT_SYSTEM_PROMPT
from schemas import PipelineResult, SentimentResult


class GraphState(TypedDict):
    """贯穿全图的状态。"""

    user_input: str
    sentiment: dict  # {label, confidence, reason}
    branch: str  # encourage / comfort / neutral_chat
    reply: str


# ---- 条件路由：label → branch（幻觉兜底：非法 label 归入 neutral_chat） ----
def route_by_sentiment(state: GraphState) -> str:
    label = state["sentiment"].get("label", "neutral")
    return LABEL_TO_BRANCH.get(label, "neutral_chat")


def _build_app():
    """构建并编译图（依赖 OPENAI_API_KEY，延迟到首次调用时执行）。"""
    from langchain_openai import ChatOpenAI

    from config import MODEL_NAME, OPENAI_API_KEY, OPENAI_BASE_URL
    from prompts import BRANCH_PROMPTS

    llm = ChatOpenAI(
        model=MODEL_NAME,
        base_url=OPENAI_BASE_URL,
        api_key=OPENAI_API_KEY,
        temperature=0.3,  # 响应生成：允许少量创造性
    )
    # ---- 进程1：情感识别（结构化输出） ----
    # 分类任务独立用 temperature=0，保证识别确定性（避免边界样例判定抖动）
    sentiment_llm = ChatOpenAI(
        model=MODEL_NAME,
        base_url=OPENAI_BASE_URL,
        api_key=OPENAI_API_KEY,
        temperature=0,
    ).with_structured_output(SentimentResult)

    def sentiment_node(state: GraphState) -> dict:
        result: SentimentResult = sentiment_llm.invoke(
            [
                ("system", SENTIMENT_SYSTEM_PROMPT),
                ("user", state["user_input"]),
            ]
        )
        return {"sentiment": result.model_dump()}

    def _make_respond_node(branch: str):
        """进程2 节点工厂：每个分支一个节点，持有各自的响应提示词。"""

        def respond_node(state: GraphState) -> dict:
            reply = llm.invoke(
                [
                    ("system", BRANCH_PROMPTS[branch]),
                    ("user", state["user_input"]),
                ]
            )
            return {"branch": branch, "reply": reply.content}

        return respond_node

    # ---- 组图 ----
    graph = StateGraph(GraphState)
    graph.add_node("sentiment", sentiment_node)
    for branch in BRANCH_PROMPTS:
        graph.add_node(branch, _make_respond_node(branch))

    graph.add_edge(START, "sentiment")
    graph.add_conditional_edges(
        "sentiment",
        route_by_sentiment,
        {branch: branch for branch in BRANCH_PROMPTS},  # 节点名与分支名一致
    )
    for branch in BRANCH_PROMPTS:
        graph.add_edge(branch, END)

    return graph.compile()


_compiled = None


def _get_app():
    global _compiled
    if _compiled is None:
        validate_settings()  # 配置缺失时给出明确报错（设计文档 §7）
        _compiled = _build_app()
    return _compiled


async def run_pipeline(text: str) -> PipelineResult:
    start = time.perf_counter()
    state = await _get_app().ainvoke({"user_input": text})
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    return PipelineResult(
        user_input=text,
        sentiment=SentimentResult(**state["sentiment"]),
        reply=state["reply"],
        branch=state["branch"],
        engine="langgraph",
        elapsed_ms=elapsed_ms,
    )
