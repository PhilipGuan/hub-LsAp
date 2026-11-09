"""LCEL 链：选表 / 生成 / 改写 / 汇总。

对应 OpenAI Agents 版的：
    Agent(output_type=...) + Runner.run(...)
"""
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from config import MODEL_NAME, OPENAI_API_KEY, OPENAI_BASE_URL
from prompts import (
    GENERATE_HUMAN, GENERATE_SYSTEM,
    REWRITE_HUMAN, REWRITE_SYSTEM,
    SELECT_HUMAN, SELECT_SYSTEM,
    SUMMARY_HUMAN, SUMMARY_SYSTEM,
)
from schemas import TableSelection


def build_model(model_name: str = MODEL_NAME) -> ChatOpenAI:
    """构建 OpenAI 兼容的聊天模型。"""
    return ChatOpenAI(
        model=model_name,
        base_url=OPENAI_BASE_URL,
        api_key=OPENAI_API_KEY,
        temperature=0,
    )


def build_chains(model: ChatOpenAI | None = None) -> dict:
    """构建四条链，返回 {select, generate, rewrite, summary}。"""
    model = model or build_model()

    select_prompt = ChatPromptTemplate.from_messages([
        ("system", SELECT_SYSTEM),
        ("human", SELECT_HUMAN),
    ])
    generate_prompt = ChatPromptTemplate.from_messages([
        ("system", GENERATE_SYSTEM),
        ("human", GENERATE_HUMAN),
    ])
    rewrite_prompt = ChatPromptTemplate.from_messages([
        ("system", REWRITE_SYSTEM),
        ("human", REWRITE_HUMAN),
    ])
    summary_prompt = ChatPromptTemplate.from_messages([
        ("system", SUMMARY_SYSTEM),
        ("human", SUMMARY_HUMAN),
    ])

    return {
        # 结构化输出：对应 OpenAI Agents 的 output_type=TableSelection
        "select": select_prompt | model.with_structured_output(
            TableSelection, method="function_calling"
        ),
        "generate": generate_prompt | model | StrOutputParser(),
        "rewrite": rewrite_prompt | model | StrOutputParser(),
        "summary": summary_prompt | model | StrOutputParser(),
    }