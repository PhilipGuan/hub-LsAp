"""Chinook SQL Agent 的 Streamlit 聊天界面。"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import streamlit as st

# 支持直接执行源码入口，无需先安装 editable 包。
PROJECT_SRC = str(Path(__file__).absolute().parents[1])
if PROJECT_SRC not in sys.path:
    sys.path.insert(0, PROJECT_SRC)

from sql_agent_langgraph.workflow import SQLAgentWorkflow  # noqa: E402

st.set_page_config(
    page_title="Chinook SQL Agent",
    page_icon="🎵",
    layout="centered",
)


def _display_column_names(columns: list[str]) -> list[str]:
    """给重复列名添加后缀，避免 JOIN 结果在字典映射时丢列。"""

    counts: dict[str, int] = {}
    used: set[str] = set()
    display_names: list[str] = []
    for name in columns:
        count = counts.get(name, 0) + 1
        candidate = name if count == 1 else f"{name} ({count})"
        while candidate in used:
            count += 1
            candidate = f"{name} ({count})"
        counts[name] = count
        used.add(candidate)
        display_names.append(candidate)
    return display_names


def _render_output(output: dict[str, Any]) -> None:
    """渲染答案、SQL、执行元数据和受限结果行。"""

    st.markdown(str(output.get("answer") or "没有返回答案。"))

    metadata: list[str] = []
    tables = output.get("tables") or []
    if tables:
        metadata.append("查询表：" + "、".join(str(table) for table in tables))
    metadata.append(f"返回 {output.get('row_count', 0)} 行")
    repairs = output.get("repair_attempts", 0)
    if repairs:
        metadata.append(f"SQL 修复 {repairs} 次")
    st.caption(" · ".join(metadata))

    sql = output.get("sql")
    if sql:
        with st.expander("查看执行 SQL"):
            st.code(str(sql), language="sql")

    columns = [str(column) for column in output.get("columns") or []]
    rows = output.get("rows") or []
    if columns:
        count = output.get("row_count", len(rows))
        with st.expander(f"查询结果（{count} 行）", expanded=bool(rows)):
            if rows:
                display_columns = _display_column_names(columns)
                records = [dict(zip(display_columns, row)) for row in rows]
                st.dataframe(records, hide_index=True)
            else:
                st.info("查询成功，但没有符合条件的数据行。")

    if output.get("truncated"):
        st.warning("结果已达到行数上限，页面只展示前面一部分数据。")

    evidence = output.get("evidence") or []
    if evidence:
        st.caption("答案依据：" + "；".join(str(item) for item in evidence))

    for caveat in output.get("caveats") or []:
        st.info(str(caveat))

    error = output.get("error")
    if error:
        st.error(f"处理未完成：{error}")


def _run_question(question: str) -> dict[str, Any]:
    """每次提问新建工作流，避免异步客户端跨 Streamlit 重跑复用。"""

    result = asyncio.run(SQLAgentWorkflow().run(question))
    return result.model_dump()


def main() -> None:
    st.title("Chinook SQL Agent")
    st.write("用中文提问，查看答案、生成的 SQL 和只读查询结果。")
    st.caption("每条提问独立查询；聊天记录仅用于当前页面展示。")

    if "messages" not in st.session_state:
        st.session_state.messages = []

    with st.sidebar:
        st.subheader("会话")
        st.write("连接到项目自带的 Chinook 数据库。")
        if st.button("清空对话"):
            st.session_state.messages = []
            st.rerun()

    api_key_ready = bool(os.getenv("OPENAI_API_KEY"))
    if not api_key_ready:
        st.warning(
            "尚未配置 OPENAI_API_KEY。请在启动网页的终端设置环境变量后重新启动。"
        )

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            if message["role"] == "user":
                st.text(message["content"])
            else:
                _render_output(message["output"])

    question = st.chat_input(
        "例如：总共有多少张专辑？",
        max_chars=2000,
        disabled=not api_key_ready,
    )
    if not question or not question.strip():
        return

    question = question.strip()
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.text(question)

    with st.chat_message("assistant"):
        with st.spinner("正在查询数据库…"):
            try:
                output = _run_question(question)
            except Exception as exc:  # noqa: BLE001 - 网页要显示稳定的错误状态
                output = {
                    "question": question,
                    "answer": "处理失败，请检查服务端日志。",
                    "error": str(exc),
                }
        _render_output(output)

    st.session_state.messages.append({"role": "assistant", "output": output})


if __name__ == "__main__":
    main()
