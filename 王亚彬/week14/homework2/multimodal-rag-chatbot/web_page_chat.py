import streamlit as st
import os
import openai
import re
from pymilvus import MilvusClient  # milvus客户端

os.environ['HF_ENDPOINT'] = 'https://hf-mirror.com'
from sentence_transformers import SentenceTransformer

bge_model = SentenceTransformer('/root/autodl-tmp/models/BAAI/bge-small-zh-v1.5')
print("Loading beg finished!")

llm_client = openai.OpenAI(
    api_key='sk-9c6195bf91f7435d88ea4b819073c92c',
    base_url='https://dashscope.aliyuncs.com/compatible-mode/v1'
)

milvus_client = MilvusClient("/root/autodl-tmp/milvus_rag.db")

rag_prompt = """基于资料回答的提问精简的回答下面的问题：{0}
相关资料: {1}
回答要求：
- 回答要客观，有逻辑，要基于只有的资料。
- 如果资料中包含图片链接，则单独一行进行输出，保留图的原始链接，不要修改任何连接路径，需要将图放在合适的相关内容的位置。
"""


def clear_chat_history():
    st.session_state.messages = [
        {"role": "system", "content": "你好，我是AI助手，可以直接与大模型对话 也 可以调用内部工具。"}
    ]


# 根据大模型的回答，在前端进行渲染
def render_markdown_with_images(llm_answer):
    pattern = re.compile(r'!\[.*?\]\((.*?)\)')  # ![alt text](image_url)
    # 记住上次渲染文字在什么地方
    last_pos = 0

    for match in pattern.finditer(llm_answer):
        # 先渲染文字部分, unsafe_allow_html允许 Markdown 里的原始 HTML 被真正渲染成元素
        st.markdown(llm_answer[last_pos:match.start()], unsafe_allow_html=True)
        # 显示图片
        img_url = match.group(1)
        if os.path.exists(img_url):
            st.image(img_url)

        last_pos = match.end()

    st.markdown(llm_answer[last_pos:], unsafe_allow_html=True)


if "messages" not in st.session_state.keys():
    st.session_state.messages = [
        {"role": "system", "content": "你好，我是AI助手，可以直接与大模型对话 也 可以调用内部工具。"}
    ]

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        if message['role'] == 'assistant':
            render_markdown_with_images(message['content'])
        else:
            st.write(message["content"])

with st.sidebar:
    st.button("清空当前聊天", on_click=clear_chat_history, use_container_width=True)

if prompt := st.chat_input(accept_file="multiple", file_type=["txt", "pdf", "jpg", "png", "jpeg", "doc", "docx"]):
    # 显示用户的输入
    with st.chat_message("user"):
        st.write(prompt.text)

    # 用户的提问先用bge编码，再去milvus向量数据库中检索
    prompt_embedding = list(bge_model.encode(prompt.text, normalize_embeddings=True))

    """
    results内部结构：
    [
      [
        {
          "id": 1,
          "distance": 1.0,
          "entity": {
            "text": "第0个chunk，包含 ![图0](images/a0.jpg)",
            "db_id": 12,
            "file_name": "报告.pdf",
            "file_path": "uploads/7f3a2b1c-4d5e-4a3b-8f2a-1b2c3d4e5f60.pdf"
          }
        },
        { "id": 2, "distance": 0.0, "entity": { "...": "..." } },
        { "id": 3, "distance": 0.0, "entity": { "...": "..." } },
        { "id": 4, "distance": 0.0, "entity": { "...": "..." } },
        { "id": 5, "distance": 0.0, "entity": { "...": "..." } }
      ]
    ]
    """
    results = milvus_client.search(
        collection_name='rag-data',
        data=[prompt_embedding],
        limit=5,
        anns_field='text_vector',
        output_fields=['text', 'db_id', 'file_name', 'file_path']
    )

    related_content = ''
    for result in results[0]:
        file_dir = os.path.basename(result['entity']['file_path']).split('.')[0]
        # 将text里面的图片路径替换成真实的图片路径
        result['entity']['text'] = result['entity']['text'].replace('images/',
                                                                    './processed/' + file_dir + '/vlm/images/')
        related_content += result['entity']['text'] + '\n'

    # 生成最终的提示词
    user_prompt = rag_prompt.format(prompt.text, related_content)
    completion = llm_client.chat.completions.create(
        model='qwen3.8-max',
        messages=[
            {'role': 'system', 'content': 'You are a helpful assistant.'},
            *st.session_state.messages[1:],  # 带上之前的聊天记录，一起给llm
            {'role': 'user', 'content': user_prompt}
        ]
    )
    # completion.choices[0] 是 Choice 对象，不是 dict,不能写completion.choices[0]['message']['content']
    llm_answer = completion.choices[0].message.content

    # 显示llm的回答
    with st.chat_message('assistant'):
        render_markdown_with_images(llm_answer)

    # 保存当前的聊天记录
    st.session_state.messages.append({'role': 'user', 'content': prompt.text})
    st.session_state.messages.append({'role': 'assistant', 'content': llm_answer})

