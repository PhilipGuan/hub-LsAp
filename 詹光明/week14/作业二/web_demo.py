"""
Streamlit 入口：

    streamlit run web_demo.py
"""
import streamlit as st

st.set_page_config(
    page_title="多模态 RAG 问答",
    page_icon="🧠",
    layout="wide",
)

navigation = st.navigation(
    [
        st.Page("web_page_upload.py", title="知识库文件", icon="📚"),
        st.Page("web_page_chat.py", title="图文问答", icon="💬"),
    ]
)
navigation.run()
