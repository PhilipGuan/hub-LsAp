from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

PROJECT_DIR = Path(__file__).resolve().parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from sql_agent.agent import AgentResult, SQLAgent  # noqa: E402
from sql_agent.catalog import DBCatalog  # noqa: E402
from sql_agent.config import settings  # noqa: E402
from sql_agent.llm import LLM  # noqa: E402
from sql_agent.trace_store import FeedbackStore, normalize_user_code  # noqa: E402

st.set_page_config(
    page_title="ChatBI · Chinook SQL Agent",
    layout="wide",
)

SAMPLE_QUESTIONS = [
    "销售额最高的前5个国家是哪些？",
    "2009年每个月的销售额是多少？",
    "每个流派有多少首歌曲？",
    "哪个歌单包含最多的摇滚歌曲？",
    "消费总额最高的前5位客户是谁？",
    "每位员工的直属下属数量是多少？",
]


@st.cache_resource(show_spinner="正在解析数据库结构...")
def get_agent(db_path: str, cache_dir: str) -> SQLAgent:
    catalog = DBCatalog(db_path, cache_dir).load()
    llm = LLM(
        settings.api_key,
        settings.base_url,
        settings.model,
        timeout=settings.llm_timeout_cap_s,
        price_input_cny_per_m=settings.price_input_cny_per_m,
        price_output_cny_per_m=settings.price_output_cny_per_m,
    )
    return SQLAgent(catalog=catalog, llm=llm, settings=settings)


@st.cache_resource(show_spinner=False)
def get_feedback_store(path: str) -> FeedbackStore:
    return FeedbackStore(path, retention_days=settings.retention_days)


def render_steps(result: AgentResult) -> None:
    for step in result.steps:
        badge = "OK" if step.status == "ok" else step.status.upper()
        st.markdown(
            f"**`{badge}` {step.name}** - {step.elapsed_ms:.0f} ms"
        )
        language = "sql" if "SELECT" in step.detail.upper() else "text"
        st.code(step.detail, language=language)


def _numeric_columns(df: pd.DataFrame) -> list[str]:
    return [
        column
        for column in df.columns
        if pd.to_numeric(df[column], errors="coerce").notna().all()
    ]


def maybe_chart(df: pd.DataFrame) -> None:
    if df.shape[1] < 2 or len(df) < 2:
        return
    numeric = _numeric_columns(df)
    if not numeric:
        return
    non_numeric = [column for column in df.columns if column not in numeric]
    if non_numeric:
        x_col = non_numeric[0]
        y_cols = numeric[:3]
    else:
        # 所有列均为数值（如 customer_id + revenue）：第一列转分类轴
        x_col = df.columns[0]
        y_cols = [column for column in df.columns[1:] if column in numeric][:3]
    if not y_cols:
        return
    chart_df = pd.DataFrame({x_col: df[x_col].astype(str)})
    for column in y_cols:
        chart_df[column] = pd.to_numeric(df[column], errors="coerce")
    chart_df = chart_df.set_index(x_col)
    looks_time = bool(
        pd.to_datetime(chart_df.index, errors="coerce").notna().all()
    )
    if looks_time:
        st.line_chart(chart_df, height=320)
    elif len(y_cols) == 1:
        st.bar_chart(chart_df, height=320)


def render_feedback(result: AgentResult, user_code: str) -> None:
    if not result.trace_id:
        return
    store = get_feedback_store(str(settings.feedback_db))
    key = result.trace_id
    st.caption(f"反馈将关联 trace_id={key}")
    col_up, col_down, col_reason = st.columns([1, 1, 4])
    with col_up:
        up = st.button("有帮助", key=f"fb_up_{key}", use_container_width=True)
    with col_down:
        down = st.button("没帮助", key=f"fb_down_{key}", use_container_width=True)
    with col_reason:
        reason = st.text_input(
            "反馈原因（可选）",
            key=f"fb_reason_{key}",
            label_visibility="collapsed",
            placeholder="例如：数字不对 / 口径理解错了 / 答案有用",
        )
    if up or down:
        try:
            store.save(
                trace_id=key,
                user_code=user_code,
                rating=1 if up else -1,
                reason=reason,
                question=result.question,
            )
        except Exception as exc:  # noqa: BLE001 - 反馈失败不阻断答案
            st.warning(f"反馈保存失败：{exc}")
        else:
            st.success("反馈已记录")


def render_meta(result: AgentResult) -> None:
    metric = result.metric or {}
    time_range = result.time_range or {}
    budget = result.budget or {}
    parts = []
    if metric.get("label"):
        parts.append(f"指标：{metric['label']}")
    if time_range.get("label"):
        parts.append(f"时间：{time_range['label']}")
    if result.data_until:
        parts.append(f"数据截止：{result.data_until}")
    parts.append(f"trace_id：{result.trace_id}")
    if budget:
        parts.append(
            f"LLM 调用：{budget.get('calls', 0)} 次 / "
            f"{budget.get('total_tokens', 0)} tokens / "
            f"￥{budget.get('cost_cny', 0):.4f}"
        )
    st.caption(" · ".join(parts))


def render_warnings(result: AgentResult) -> None:
    if not result.warnings:
        return
    with st.expander(f"告警与口径提示（{len(result.warnings)}）", expanded=False):
        for warning in result.warnings:
            code = warning.get("code", "")
            detail = warning.get("detail", "")
            severity = warning.get("severity", "info")
            st.markdown(f"- `{code}` {detail}" + (f"（{severity}）" if severity else ""))


def render_retry(result: AgentResult) -> None:
    key = result.trace_id or result.question
    if st.button("重试这个问题", key=f"retry_{key}"):
        st.session_state.seed_prompt = result.question
        st.rerun()


def render_result(result: AgentResult, user_code: str) -> None:
    if not result.success:
        st.error(result.user_message or result.error or "查询失败")
        if result.error_code:
            st.caption(
                f"错误码：{result.error_code} "
                f"{result.error_name}（error_class={result.error_class}）"
            )
        if result.answer and result.answer != result.user_message:
            st.markdown(result.answer)
        for suggestion in result.suggestions:
            if st.button(suggestion, key=f"sug_{result.trace_id}_{suggestion}"):
                st.session_state.seed_prompt = suggestion
                st.rerun()
        render_retry(result)
        with st.expander("查看执行过程", expanded=True):
            render_steps(result)
        with st.expander("反馈", expanded=False):
            render_feedback(result, user_code)
        return

    st.markdown(result.answer)
    render_meta(result)
    st.caption(
        f"选中表：{', '.join(result.tables) or '无'} · "
        f"尝试 {result.attempts} 次 · 总耗时 {result.total_ms:.0f} ms"
    )
    render_warnings(result)

    with st.expander("查看执行过程", expanded=False):
        render_steps(result)
    if result.assumptions:
        with st.expander("口径与假设", expanded=False):
            for item in result.assumptions:
                st.markdown(f"- {item}")

    tab_data, tab_sql, tab_meta, tab_feedback = st.tabs(
        ["结果数据", "SQL", "口径与时间", "反馈"]
    )
    with tab_data:
        df = result.to_dataframe()
        st.dataframe(df, use_container_width=True, hide_index=True)
        if result.truncated:
            st.caption(f"结果已截断，仅展示前 {settings.max_rows} 行。")
        maybe_chart(df)
    with tab_sql:
        st.code(result.sql or "（未生成 SQL）", language="sql")
    with tab_meta:
        st.json(
            {
                "metric": result.metric,
                "time_range": result.time_range,
                "data_until": result.data_until,
                "empty_reason": result.empty_reason,
                "status": result.status,
                "row_count": result.row_count,
                "truncated": result.truncated,
                "masked_columns": result.masked_columns,
                "budget": result.budget,
            }
        )
    with tab_feedback:
        render_feedback(result, user_code)


def active_user_code() -> str:
    raw = st.session_state.get("user_code", "local")
    try:
        return normalize_user_code(raw)
    except ValueError:
        return "local"


def main() -> None:
    st.title("ChatBI · Chinook SQL Agent")
    st.caption("中文提问 → 意图门禁 → 选表 → 生成 SQL → 校验/改写 → 执行 → 受约束汇总")

    if not settings.api_key:
        st.error("未配置 DeepSeek API Key，无法启动。")
        st.markdown("请在项目根目录创建 `.env` 文件，内容如下：")
        st.code(
            "DEEPSEEK_API_KEY=sk-你的key\n"
            "DEEPSEEK_BASE_URL=https://api.deepseek.com\n"
            "DEEPSEEK_MODEL=deepseek-chat",
            language="bash",
        )
        st.stop()

    try:
        agent = get_agent(str(settings.db_path), str(settings.cache_dir))
    except Exception as exc:  # noqa: BLE001
        st.error(f"初始化失败：{exc}")
        st.stop()

    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "user_code" not in st.session_state:
        st.session_state.user_code = "local"

    with st.sidebar:
        st.header("配置")
        st.text_input(
            "试点用户码",
            key="user_code",
            help="2-16 位字母、数字、下划线或短横线；用于关联反馈。",
        )
        try:
            normalize_user_code(st.session_state.user_code)
        except ValueError as exc:
            st.warning(str(exc))

        st.write(f"**数据库**：`{settings.db_path.name}`")
        st.write(f"**表数量**：{len(agent.catalog.table_names)}")
        st.write(f"**数据范围**：{agent.catalog.data_from} ~ {agent.catalog.data_until}")
        st.write(f"**模型**：`{settings.model}`")
        st.write(f"**最大返回行数**：{settings.max_rows}")
        st.write(f"**最大改写次数**：{settings.max_rewrites}")

        store = get_feedback_store(str(settings.feedback_db))
        summary = store.summary()
        rate = summary["positive_rate"]
        st.write(
            f"**反馈**：{summary['total']} 条，赞 {summary['up']} / 踩 {summary['down']}"
            + (f"，好评率 {rate:.0%}" if rate is not None else "")
        )

        if st.button("刷新数据库解析缓存", use_container_width=True):
            DBCatalog(settings.db_path, settings.cache_dir).load(force=True)
            st.cache_resource.clear()
            st.success("缓存已刷新")
            st.rerun()

        if st.button("清空对话", use_container_width=True):
            st.session_state.messages = []
            st.rerun()

        st.divider()
        st.subheader("示例问题")
        for question in SAMPLE_QUESTIONS:
            if st.button(question, use_container_width=True):
                st.session_state.seed_prompt = question
                st.rerun()

    user_code = active_user_code()
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            if message["role"] == "assistant" and "result" in message:
                render_result(message["result"], user_code)
            else:
                st.markdown(message["content"])

    prompt = st.chat_input("用中文提问，例如：销售额最高的前5个国家是哪些？")
    seeded = st.session_state.pop("seed_prompt", None)
    prompt = prompt or seeded

    if prompt:
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            with st.spinner("正在选表、生成并校验 SQL..."):
                result = agent.run(prompt, user_code=user_code)
            render_result(result, user_code)

        st.session_state.messages.append(
            {"role": "assistant", "content": result.answer, "result": result}
        )


if __name__ == "__main__":
    main()
