import os
import subprocess
from kafka import KafkaConsumer
import json
import glob
import numpy as np
os.environ['HF_ENDPOINT'] = 'https://hf-mirror.com'
from sentence_transformers import SentenceTransformer
from pymilvus import MilvusClient  # milvus客户端


client = MilvusClient('/root/autodl-tmp/milvus_rag.db')

print('loading bge...')
bge_model = SentenceTransformer('/root/autodl-tmp/models/BAAI/bge-small-zh-v1.5')
print('finished')

print('loading clip...')
clip_model = SentenceTransformer('/root/autodl-tmp/models/jinaai/jina-clip-v2',
                                 trust_remote_code=True, truncate_dim=1024)
print('finished')


def split_text2chunks(lines, chunk_size=256):
    chunks = []
    for line in lines:
        line = line.strip()

        # 过滤掉无关内容
        if not line:
            continue
        if line == '# References':
            continue
        if len(line) > 2 and line[0] == '[' and line[1].isdigit():
            continue

        if len(chunks) == 0:
            chunks.append(line)
        else:
            if len(chunks[-1]) < 256:
                chunks[-1] += '\n' + line
            else:
                chunks.append(line)

    return chunks


def encode_text_and_image(text, markdown_path):
    # 提取纯文本连接起来
    text_with_no_image = '\n'.join([line for line in text.split('\n') if not line.startswith("![")])
    # 再次提取，只要图片引用
    text_with_image = [line for line in text.split('\n') if line.startswith('![')]

    # bge、clip文本编码
    try:
        text_bge_embedding = list(bge_model.encode(text_with_no_image, normalize_embeddings=True))
    except Exception as e:
        print(e)
        text_bge_embedding = list(np.zeros(512))
    try:
        text_clip_embedding = list(clip_model.encode(text_with_no_image, normalize_embeddings=True))
    except Exception as e:
        print(e)
        text_clip_embedding = list(np.zeros(1024))

    # clip图片编码,先得到图片的真实路径,# text_with_image内部元素：![图1](images/a.jpg)，默认只有一个,其实不太合理
    if len(text_with_image) > 0:
        image_path = text_with_image[0].split('](')[1].split(')')[0]
        image_path_real = os.path.join(os.path.dirname(markdown_path), image_path)

        try:
            image_clip_embedding = list(clip_model.encode(image_path_real, normalize_embeddings=True))
        except Exception as e:
            print(e)
            image_clip_embedding = list(np.zeros(1024))
    else:
        image_clip_embedding = list(np.zeros(1024))

    return text_bge_embedding, text_clip_embedding, image_clip_embedding


def encode_document(path, file_name, file_path, file_id):
    with open(path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    chunks = split_text2chunks(lines)
    for chunk in chunks:
        try:
            text_bge_embedding, text_clip_embedding, image_clip_embedding = encode_text_and_image(chunk, path)

            data = {
                "text_vector": text_bge_embedding,
                "clip_text_vector": text_clip_embedding,
                "clip_image_vector": image_clip_embedding,
                "text": chunk,
                "db_id": file_id,
                "file_name": file_name,
                "file_path": file_path
            }

            client.insert(
                collection_name="rag-data",
                data=data
            )
        except Exception as e:
            print(e)


consumer = KafkaConsumer(
    'rag-data',
    bootstrap_servers='localhost:9092',
    enable_auto_commit=True,
    value_deserializer=lambda v: json.loads(v.decode('utf-8'))
)

for msg in consumer:
    try:
        print('msg=', msg.value)
        file_name = msg.value['file_name']
        file_path = msg.value['file_path']
        file_id = msg.value['file_id']

        if not os.path.exists(file_path):
            continue

        # 利用mineru解析文档,先启动服务mineru-vllm-server --port 30000
        print(f"执行命令mineru -p {file_path} -o ./processed -b vlm-http-client -u http://127.0.0.1:30000，解析文档...")
        subprocess.check_call(f"mineru -p {file_path} -o ./processed -b vlm-http-client -u http://127.0.0.1:30000", shell=True, timeout=600)
        print(f'找到为{file_name}生成的markdown文件')
        markdown_file_paths = glob.glob(os.path.join('./processed', os.path.basename(file_path).split('.')[0], '**/**.md'))

        if len(markdown_file_paths) == 0:
            print('找不到markdown文件')
            continue

        encode_document(markdown_file_paths[0], file_name, file_path, file_id)

    except Exception as e:
        print(e)






