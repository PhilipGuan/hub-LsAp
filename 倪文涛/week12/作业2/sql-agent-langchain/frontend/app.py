from pathlib import Path
import sys

import pandas as pd
import requests
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from frontend.sse import parse_events

st.set_page_config(page_title="Chinook 数据智能问答", page_icon="💬", layout="wide")
st.title("Chinook 数据智能问答")
st.caption("用自然语言查询音乐销售数据，实时查看查询进度和回答。")


def show_result(record, table_slot, detail_slot, show_table=True):
    with table_slot.container():
        if show_table and record.get("columns"):
            # Arrow 表格要求列名唯一；SQL 原始列名仍在接口结果中保留。
            used = set()
            names = []
            for index, name in enumerate(record["columns"]):
                label = name
                while label in used:
                    label += f" ({index + 1})"
                used.add(label)
                names.append(label)
            st.dataframe(pd.DataFrame(record.get("rows", []), columns=names), hide_index=True, width="stretch")
        if show_table and record.get("truncated"):
            st.caption(f"结果已截断，仅展示前 {record.get('returned_rows', 0)} 行。")
    with detail_slot.container():
        with st.expander("查看 SQL 与执行过程"):
            st.write("选中的表：" + "、".join(record.get("selected_tables", [])))
            if record.get("sql"):
                st.code(record["sql"], language="sql")
                st.json(record.get("params", {}))
            st.write(f"SQL 改写次数：{record.get('rewrite_count', 0)}")
            for step in record.get("steps", []):
                if step["stage"] == "summary_reasoning":
                    continue
                st.caption(f"{step['stage']} · {step['status']} · {step['message']}")


if "messages" not in st.session_state:
    st.session_state.messages = []
if st.sidebar.button("清空对话", use_container_width=True):
    st.session_state.messages = []
    st.rerun()

for record in st.session_state.messages:
    with st.chat_message(record["role"]):
        if record["role"] == "assistant":
            progress, detail_slot, answer_slot, table_slot = [st.empty() for _ in range(4)]
            answer_slot.markdown(record.get("answer", ""))
            if record.get("status") in ("failed", "interrupted"):
                progress.error(record.get("notice", "本次查询未完成。"))
            if record.get("summary_degraded"):
                progress.warning("自动汇总暂不可用，已保留查询结果。")
            show_result(record, table_slot, detail_slot)
        else:
            st.markdown(record.get("answer", ""))

question = st.chat_input("例如：累计消费最高的前 5 位客户是谁？", max_chars=2000)
if question and question.strip():
    st.session_state.messages.append({"role": "user", "answer": question.strip()})
    with st.chat_message("user"):
        st.markdown(question)
    record = {"role": "assistant", "answer": "", "steps": [], "status": "interrupted"}
    finished = False
    with st.chat_message("assistant"):
        progress, detail_slot, answer_slot, table_slot = [st.empty() for _ in range(4)]
        progress.info("正在连接查询服务…")
        try:
            with requests.post("http://127.0.0.1:8000/api/chat", json={"question": question.strip()},
                               headers={"Accept": "text/event-stream"}, stream=True,
                               timeout=(5, 45)) as response:
                response.raise_for_status()
                if "text/event-stream" not in response.headers.get("Content-Type", ""):
                    raise ValueError("后端未返回 SSE 事件流。")
                response.encoding = "utf-8"
                for event, data in parse_events(response.iter_lines(chunk_size=1, decode_unicode=True)):
                    if event == "step":
                        if data["stage"] == "summary_reasoning":
                            continue
                        record["steps"].append(data)
                        progress.info(data["message"])
                    elif event == "result":
                        record.update(data)
                        show_result(record, table_slot, detail_slot, show_table=False)
                    elif event == "answer_delta":
                        record["answer"] += data["delta"]
                        answer_slot.markdown(record["answer"] + " ▌")
                    elif event == "error":
                        record["notice"] = data["message"]
                        progress.warning(data["message"])
                    elif event == "done":
                        record.update(data)
                        finished = True
                        answer_slot.markdown(record["answer"])
                        show_result(record, table_slot, detail_slot)
                        break
                if not finished:
                    record["notice"] = "连接已中断，当前回答可能不完整，请重新提交。"
        except (requests.RequestException, ValueError, KeyError, TypeError):
            record["notice"] = "请求失败或连接中断，请确认后端已启动后重新提交。"
        finally:
            if not finished:
                record["status"] = "interrupted"
            st.session_state.messages.append(record)
    st.rerun()
