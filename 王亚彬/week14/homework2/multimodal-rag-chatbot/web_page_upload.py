import json

import streamlit as st
import os
from orm_model import File, Session
import uuid
from pymilvus import MilvusClient
from kafka import KafkaProducer

# 用"本地单文件"模式启动 Milvus，不连服务器
milvus_client = MilvusClient('/root/autodl-tmp/milvus_rag.db')


"""查询所有文件并展示，每个文件旁边添加删除按钮"""
def query_files():
    with Session() as session:
        files = session.query(File).all()

    if files:
        for file in files:
            # 创建一个删除按钮，按钮的 key 是文件的 ID
            button = st.button(f"删除{file.filename}", key=file.id)

            # 如果用户点击了删除按钮
            if button:
                delete_file(file.id)

            # 展示文件信息
            st.write(file)

    else:
        st.info("没有找到任何文件")


# sqlite, 后端本地和milvus同步删除一条文件数据
def delete_file(file_id: int):
    with Session() as session:
        # 先查出来再删除
        file = session.query(File).filter(File.id == file_id).first()
        if file:
            session.delete(file)
            if os.path.exists(file.filepath):
                os.remove(file.filepath)
            session.commit()
            st.success("删除成功")
        else:
            st.error("无效文件，删除失败")

        milvus_client.delete(collection_name="rag_data", filter=f"db_id == {file_id}")


st.markdown("### 文件管理")
query_files()
st.markdown("### 文件上传")

# 上传文件需要保存三个地方
upload_file = st.file_uploader("上传文件", type=['pdf', 'docx', 'txt'])
if upload_file:
    os.makedirs('uploads', exist_ok=True)

    file_name = upload_file.name
    file_extension = os.path.splitext(file_name)[1]
    save_name = str(uuid.uuid4()) + file_extension
    save_path = os.path.join('uploads', save_name)

    # 保存到后端本地
    with open(save_path, 'wb') as f:
        f.write(upload_file.getvalue())

    # files表中记录一份
    with Session() as session:
        file = File(
            filename=file_name,
            filepath=save_path,
            filestate='已上传'
        )

        session.add(file)
        session.commit()
        file_id = file.id

    # milvus存一份，通过kafka
    producer = KafkaProducer(
        bootstrap_servers='localhost:9092',
        value_serializer=lambda v: json.dumps(v).encode('utf-8')
    )

    producer.send(
        topic='rag-data',
        value={'file_name': file_name, 'file_path': save_path, 'file_id': file_id}
    )

    producer.flush()


