# -*- coding: utf-8 -*-
"""读取汽车知识手册，分块 + 生成向量，创建索引并批量写入 ES。"""

from elasticsearch.helpers import bulk
from sentence_transformers import SentenceTransformer

import config
from common import get_es, split_text, read_pdf_pages


def get_embedding_dim(model):
    """兼容不同版本 sentence_transformers 获取向量维度。"""
    try:
        return model.get_embedding_dimension()
    except AttributeError:
        return model.get_sentence_embedding_dimension()


def build_documents(pages, model):
    """把每页分块并编码，生成文档列表。"""
    docs = []
    for page in pages:
        chunks = split_text(page["content"])
        for i, chunk in enumerate(chunks):
            docs.append({
                "page": page["page"],
                "page_num": page["page_num"],
                "chunk_idx": i,
                "content": chunk,
            })

    texts = [d["content"] for d in docs]
    vectors = model.encode(texts, normalize_embeddings=True, show_progress_bar=True)
    for d, vec in zip(docs, vectors):
        d["content_vector"] = vec.tolist()
    return docs


def create_index(es, dims):
    """删除旧索引并新建（含 dense_vector 字段）。"""
    if es.indices.exists(index=config.INDEX_NAME):
        es.indices.delete(index=config.INDEX_NAME)
        print(f"旧索引 '{config.INDEX_NAME}' 已删除。")

    es.indices.create(
        index=config.INDEX_NAME,
        body={
            "settings": {"number_of_shards": 1, "number_of_replicas": 0},
            "mappings": {
                "properties": {
                    "page": {"type": "keyword"},
                    "page_num": {"type": "integer"},
                    "chunk_idx": {"type": "integer"},
                    "content": {
                        "type": "text",
                        "analyzer": "cjk",
                    },
                    "content_vector": {
                        "type": "dense_vector",
                        "dims": dims,
                        "index": True,
                        "similarity": "cosine",
                    },
                }
            },
        },
    )
    print(f"索引 '{config.INDEX_NAME}' 创建成功，向量维度 {dims}")


def main():
    es = get_es()
    model = SentenceTransformer(config.MODEL_PATH)
    dims = get_embedding_dim(model)

    pages = read_pdf_pages(config.PDF_PATH)
    print(f"PDF 共 {len(pages)} 页")

    docs = build_documents(pages, model)
    print(f"共生成 {len(docs)} 个 chunk")

    create_index(es, dims)

    actions = [
        {"_index": config.INDEX_NAME, "_id": f"{d['page']}_{d['chunk_idx']}", "_source": d}
        for d in docs
    ]
    success, errors = bulk(es, actions, chunk_size=200)
    print(f"写入完成：成功 {success}，失败 {len(errors) if errors else 0}")

    es.indices.refresh(index=config.INDEX_NAME)
    print("索引已刷新，可进行检索。")


if __name__ == "__main__":
    main()
