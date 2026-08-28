"""第二周作业：人物关系分析智能体 Streamlit 界面。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import matplotlib.pyplot as plt
import networkx as nx
import streamlit as st
from dotenv import load_dotenv
from matplotlib import font_manager

from relationship_agent import (
    Relationship,
    RelationshipAgent,
    RelationshipResult,
    relationships_as_list,
)


APP_DIR = Path(__file__).resolve().parent
load_dotenv(APP_DIR / ".env")

st.set_page_config(
    page_title="人物关系分析智能体",
    page_icon="🕸️",
    layout="wide",
)


def configure_chinese_font() -> str:
    """优先使用 Windows 自带中文字体，避免图谱标签显示为方框。"""

    candidates = [
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
    ]
    for font_path in candidates:
        if font_path.exists():
            font_manager.fontManager.addfont(str(font_path))
            name = font_manager.FontProperties(fname=str(font_path)).get_name()
            plt.rcParams["font.sans-serif"] = [name]
            plt.rcParams["axes.unicode_minus"] = False
            return name
    return "sans-serif"


def draw_relationship_graph(relationships: list[Relationship]) -> None:
    graph = nx.DiGraph()
    for item in relationships:
        graph.add_node(item.source)
        graph.add_node(item.target)
        if graph.has_edge(item.source, item.target):
            old_label = graph[item.source][item.target]["label"]
            graph[item.source][item.target]["label"] = f"{old_label} / {item.relation}"
        else:
            graph.add_edge(item.source, item.target, label=item.relation)

    if not graph.nodes:
        st.info("没有明确人物关系，因此图谱为空。")
        return

    font_family = configure_chinese_font()
    figure, axis = plt.subplots(figsize=(9, 5.5))
    positions = nx.spring_layout(graph, seed=42, k=1.4)
    nx.draw_networkx_nodes(
        graph,
        positions,
        node_size=3000,
        node_color="#DDEBFF",
        edgecolors="#3B82F6",
        linewidths=1.8,
        ax=axis,
    )
    nx.draw_networkx_labels(
        graph,
        positions,
        font_size=13,
        font_weight="bold",
        font_family=font_family,
        ax=axis,
    )
    nx.draw_networkx_edges(
        graph,
        positions,
        edge_color="#64748B",
        width=2,
        arrows=True,
        arrowsize=22,
        connectionstyle="arc3,rad=0.08",
        min_source_margin=24,
        min_target_margin=24,
        ax=axis,
    )
    edge_labels = nx.get_edge_attributes(graph, "label")
    nx.draw_networkx_edge_labels(
        graph,
        positions,
        edge_labels=edge_labels,
        font_size=11,
        font_family=font_family,
        label_pos=0.5,
        rotate=False,
        ax=axis,
    )
    axis.set_axis_off()
    figure.tight_layout()
    st.pyplot(figure, width="stretch")
    plt.close(figure)


st.title("🕸️ 人物关系分析智能体")
st.caption("使用大模型 JSON Mode 或 Tool Calling，将自然语言转换成人物关系图谱。")

with st.sidebar:
    st.header("大模型配置")
    api_key = st.text_input(
        "API Key",
        value=os.getenv("LLM_API_KEY", ""),
        type="password",
        help="仅在当前会话中使用，页面不会显示完整密钥。",
    )
    base_url = st.text_input(
        "Base URL",
        value=os.getenv("LLM_BASE_URL", "https://api.deepseek.com"),
    )
    model = st.text_input(
        "模型名称",
        value=os.getenv("LLM_MODEL", "deepseek-chat"),
    )
    mode_label = st.radio(
        "结构化输出方式",
        ["JSON Mode", "Tool Calling"],
        help="两种方式均满足作业要求。",
    )
    st.divider()
    st.markdown("**隐私提示**：不要把包含真实 API Key 的 `.env` 文件提交给老师。")

text = st.text_area(
    "输入人物关系描述",
    value="小明喜欢小姚，但是小姚喜欢小王。",
    height=130,
    placeholder="例如：张三和李四是朋友，李四讨厌王五。",
)

analyze_col, demo_col, _ = st.columns([1, 1, 3])
with analyze_col:
    analyze_clicked = st.button("开始分析", type="primary", width="stretch")
with demo_col:
    demo_clicked = st.button(
        "查看示例结果",
        width="stretch",
        help="不调用 API，仅用于预览界面。",
    )

if demo_clicked:
    st.session_state.relationship_result = RelationshipResult(
        relationships=[
            Relationship(source="小明", relation="爱慕", target="小姚"),
            Relationship(source="小姚", relation="爱慕", target="小王"),
        ]
    )
    st.session_state.result_source = "内置示例（未调用大模型）"

if analyze_clicked:
    if not text.strip():
        st.warning("请先输入人物关系描述。")
    elif not api_key.strip():
        st.error("请在侧边栏填写 API Key，或在 `.env` 中配置 LLM_API_KEY。")
    else:
        mode = "json" if mode_label == "JSON Mode" else "tool"
        try:
            with st.spinner("大模型正在抽取全部人物关系……"):
                agent = RelationshipAgent(
                    api_key=api_key.strip(),
                    base_url=base_url.strip() or None,
                    model=model.strip(),
                )
                st.session_state.relationship_result = agent.extract(text, mode=mode)
                st.session_state.result_source = f"大模型 · {mode_label}"
        except Exception as exc:
            st.error(f"分析失败：{exc}")

result: RelationshipResult | None = st.session_state.get("relationship_result")
if result is not None:
    st.divider()
    source = st.session_state.get("result_source", "")
    st.subheader("分析结果")
    st.caption(source)

    json_data = relationships_as_list(result)
    json_text = json.dumps(json_data, ensure_ascii=False, indent=2)

    json_col, graph_col = st.columns([1, 1.4])
    with json_col:
        st.markdown("#### 结构化 JSON")
        st.code(json_text, language="json")
        st.download_button(
            "下载 JSON",
            data=json_text.encode("utf-8"),
            file_name="relationships.json",
            mime="application/json",
            width="stretch",
        )
        st.metric("关系数量", len(json_data))

    with graph_col:
        st.markdown("#### 人物关系图谱")
        draw_relationship_graph(result.relationships)

with st.expander("查看抽取规则"):
    st.markdown(
        """
        - 提取文本中所有明确出现的人物关系。
        - `source` 是关系发起者，`target` 是关系指向者。
        - 喜欢、暗恋、爱上统一为“爱慕”；讨厌、憎恨统一为“厌恶”。
        - 不推测文本中没有表达的关系；没有关系时返回空数组。
        """
    )
