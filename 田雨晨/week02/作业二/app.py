"""
情感分析智能体 - 人物关系抽取
基于LLM Tool Call能力，从文本中提取人物关系并输出JSON格式的关系图谱
"""

import streamlit as st
import os
from anthropic import Anthropic
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 页面配置
st.set_page_config(
    page_title="情感分析智能体",
    page_icon="💕",
    layout="centered"
)

# 自定义样式
st.markdown("""
<style>
    .main {
        background-color: #f5f5f5;
    }
    .stTextInput > div > div > input {
        border-radius: 10px;
    }
    .result-box {
        background-color: white;
        border-radius: 10px;
        padding: 20px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
    }
</style>
""", unsafe_allow_html=True)


def get_anthropic_client(api_key: str = None):
    """获取Anthropic客户端"""
    # 支持自定义 API 地址
    base_url = os.getenv("ANTHROPIC_BASE_URL", "https://api.siliconflow.cn")
    # 默认 API Key
    default_api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        api_key = default_api_key
    if not api_key:
        return None
    return Anthropic(
        api_key=api_key,
        base_url=base_url
    )


def extract_relations(client: Anthropic, text: str) -> list:
    """
    使用LLM Tool Call从文本中提取人物关系
    """
    # 定义tool schema
    tools = [
        {
            "name": "extract_relations",
            "description": "从文本中提取人物关系，构建知识图谱",
            "input_schema": {
                "type": "object",
                "properties": {
                    "relations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "source": {
                                    "type": "string",
                                    "description": "关系主体（人物）"
                                },
                                "relation": {
                                    "type": "string",
                                    "description": "关系类型（如：喜欢、爱慕、讨厌、朋友等）"
                                },
                                "target": {
                                    "type": "string",
                                    "description": "关系客体（人物）"
                                }
                            },
                            "required": ["source", "relation", "target"]
                        },
                        "description": "提取的人物关系列表"
                    }
                },
                "required": ["relations"]
            }
        }
    ]

    system_prompt = """你是一个关系抽取专家。请仔细分析用户输入的文本，提取其中的人物关系。

## 核心规则（必须遵守）：
1. 用逗号分隔的多个短句，每个短句都要提取关系
2. 用句号分隔的句子，每个句子都要提取关系
3. 用"但是"、"不过"、"然而"等转折词连接的内容，要分别提取每个部分的关系
4. 关系必须在文本中明确出现，不能推断

## 关系类型：
- 喜欢、爱慕、暗恋、爱上
- 讨厌、怨恨、厌恶、不喜欢
- 朋友、闺蜜、兄弟、死党
- 家人（父母、子女、夫妻、父子、母子等）
- 同学、同事

## 输出格式：
必须是JSON数组，每个元素包含：
- source：关系主体（谁）
- relation：关系类型（喜欢、讨厌等）
- relation_target：关系客体（被谁）

## 关键示例（必须按此格式输出）：
输入："小明喜欢小美，小美喜欢小姚"
输出必须包含2个关系：
- 小明 → 喜欢 → 小美
- 小美 → 喜欢 → 小姚

输入："小明喜欢小美，但是小美喜欢小王"
输出必须包含2个关系：
- 小明 → 喜欢 → 小美
- 小美 → 喜欢 → 小王

请严格按照上述格式输出JSON数组。"""

    # 调用 API
    response = client.messages.create(
        model="Pro/MiniMaxAI/MiniMax-M2.5",
        max_tokens=2048,
        system=system_prompt,
        messages=[{"role": "user", "content": text}],
    )

    # 解析响应
    for content in response.content:
        # 情况1: tool_use 类型
        if content.type == "tool_use":
            return content.input.get("relations", [])

        # 情况2: text 类型，需要解析 JSON
        if content.type == "text":
            import json
            try:
                # 尝试直接解析 JSON 数组
                relations = json.loads(content.text)
                if isinstance(relations, list):
                    return relations
            except json.JSONDecodeError:
                pass
            # 如果解析失败，尝试提取 JSON 部分
            import re
            json_match = re.search(r'\[.*\]', content.text, re.DOTALL)
            if json_match:
                try:
                    relations = json.loads(json_match.group())
                    return relations
                except:
                    pass

    return []


def main():
    """主函数"""
    st.title("💕 情感分析智能体")
    st.markdown("### 人物关系图谱抽取")

    # 初始化 session_state
    if "input_text" not in st.session_state:
        st.session_state.input_text = ""

    # API Key输入（支持默认值或用户自定义）
    default_api_key = os.getenv("ANTHROPIC_API_KEY", "sk-vcmmxkkwqzfpdmfqpvthmzfkwmibehynszsydzwxqfvxdmxz")
    api_key = st.text_input(
        "API Key（可选，不输入则使用默认）",
        type="password",
        value="",
        help="默认使用预设的API Key，也可输入自己的Key"
    )
    if not api_key:
        api_key = default_api_key

    # 关系示例
    examples = [
        "小明喜欢小姚，但是小姚喜欢小王。",
        "小红是小明的女朋友，他们在一起三年了。",
        "张三和李四是大学同学，关系很好。",
        "王五是小王的父亲对小王非常严厉。"
    ]

    # 示例按钮（必须放在输入框之前）
    st.markdown("**示例：**")
    cols = st.columns(2)
    for i, example in enumerate(examples):
        with cols[i % 2]:
            if st.button(f"示例{i+1}", key=f"example_{i}"):
                st.session_state.input_text = example
                st.rerun()

    # 输入区域
    st.markdown("#### 📝 输入文本")
    input_text = st.text_area(
        "请输入包含人物关系的文本：",
        value=st.session_state.input_text,
        height=100,
        placeholder="例如：小明喜欢小姚，但是小姚喜欢小王。",
        key="input_text"
    )

    # 分析按钮
    if st.button("🔍 分析关系", type="primary"):
        if not api_key:
            st.error("请先输入 API Key！")
            return

        if not input_text.strip():
            st.warning("请输入要分析的文本！")
            return

        try:
            with st.spinner("🤔 智能分析中..."):
                client = get_anthropic_client(api_key)
                relations = extract_relations(client, input_text)

                if relations:
                    st.success("✅ 分析完成！")

                    # 显示关系图谱
                    st.markdown("#### 🕸️ 人物关系图谱")
                    st.markdown('<div class="result-box">', unsafe_allow_html=True)

                    # 格式化输出（按 source -> relation -> target 顺序）
                    import json
                    # 重新排序键的顺序，兼容 target 和 relation_target
                    sorted_relations = []
                    for rel in relations:
                        sorted_relations.append({
                            "source": rel.get("source", ""),
                            "relation": rel.get("relation", ""),
                            "target": rel.get("target") or rel.get("relation_target", "")
                        })
                    st.code(json.dumps(sorted_relations, ensure_ascii=False, indent=4), language="json")

                    st.markdown('</div>', unsafe_allow_html=True)

                    # 关系可视化（简单展示）
                    st.markdown("#### 📊 关系摘要")
                    for rel in relations:
                        target = rel.get("target") or rel.get("relation_target", "")
                        st.write(f"👤 {rel['source']} → {rel['relation']} → {target}")
                else:
                    st.warning("未检测到明确的人物关系")

        except Exception as e:
            st.error(f"分析失败: {str(e)}")


if __name__ == "__main__":
    main()
