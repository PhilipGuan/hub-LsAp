"""第六周作业：Elasticsearch 全文检索、条件过滤和向量检索。"""

import json
from pathlib import Path

from elasticsearch import Elasticsearch
from sentence_transformers import SentenceTransformer


BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "作业输出"
OUTPUT_DIR.mkdir(exist_ok=True)
MODEL_PATH = BASE_DIR.parents[1] / "week05" / "models" / "bge-small-zh-v1.5"
INDEX_NAME = "week06_homework_products"

es = Elasticsearch("http://127.0.0.1:9200")
info = es.info()
print(f"Elasticsearch 连接成功：version={info['version']['number']}，cluster={info['cluster_name']}")

print(f"加载本地向量模型：{MODEL_PATH}")
model = SentenceTransformer(str(MODEL_PATH))
documents = [
    {
        "product_id": "A001",
        "name": "智能手机",
        "description": "新款人工智能手机，支持自然语言助手和高清摄影。",
        "category": "电子产品",
        "price": 4999.0,
        "on_sale": True,
    },
    {
        "product_id": "B002",
        "name": "无线蓝牙耳机",
        "description": "音质清晰，佩戴舒适，适合运动和通勤。",
        "category": "电子产品",
        "price": 699.0,
        "on_sale": True,
    },
    {
        "product_id": "C003",
        "name": "机器学习入门书",
        "description": "介绍人工智能、神经网络与自然语言处理。",
        "category": "图书",
        "price": 89.0,
        "on_sale": False,
    },
    {
        "product_id": "D004",
        "name": "篮球",
        "description": "适合室内外训练的耐磨篮球。",
        "category": "运动用品",
        "price": 199.0,
        "on_sale": True,
    },
]

embeddings = model.encode(
    [f"{item['name']}。{item['description']}" for item in documents],
    normalize_embeddings=True,
)
vector_dims = int(embeddings.shape[1])

if es.indices.exists(index=INDEX_NAME):
    es.indices.delete(index=INDEX_NAME)

es.indices.create(
    index=INDEX_NAME,
    mappings={
        "properties": {
            "product_id": {"type": "keyword"},
            "name": {"type": "text", "analyzer": "standard"},
            "description": {"type": "text", "analyzer": "standard"},
            "category": {"type": "keyword"},
            "price": {"type": "float"},
            "on_sale": {"type": "boolean"},
            "text_vector": {
                "type": "dense_vector",
                "dims": vector_dims,
                "index": True,
                "similarity": "cosine",
            },
        }
    },
)

for document, vector in zip(documents, embeddings):
    es.index(
        index=INDEX_NAME,
        id=document["product_id"],
        document={**document, "text_vector": vector.tolist()},
    )
es.indices.refresh(index=INDEX_NAME)
print(f"索引创建完成：index={INDEX_NAME}，文档数={len(documents)}，向量维度={vector_dims}")


def compact_hits(response):
    return [
        {
            "score": round(float(hit["_score"]), 4) if hit.get("_score") is not None else None,
            "product_id": hit["_source"]["product_id"],
            "name": hit["_source"]["name"],
            "category": hit["_source"]["category"],
            "price": hit["_source"]["price"],
        }
        for hit in response["hits"]["hits"]
    ]


fulltext_response = es.search(
    index=INDEX_NAME,
    query={"multi_match": {"query": "人工智能", "fields": ["name", "description"]}},
    source_excludes=["text_vector"],
)
fulltext_hits = compact_hits(fulltext_response)
print("\n[1] 全文检索：人工智能")
for hit in fulltext_hits:
    print(hit)

filter_response = es.search(
    index=INDEX_NAME,
    query={
        "bool": {
            "filter": [
                {"term": {"category": "电子产品"}},
                {"term": {"on_sale": True}},
                {"range": {"price": {"lt": 1000}}},
            ]
        }
    },
    source_excludes=["text_vector"],
)
filter_hits = compact_hits(filter_response)
print("\n[2] 条件过滤：电子产品 + 促销中 + 价格低于 1000")
for hit in filter_hits:
    print(hit)

query_text = "与 AI 和自然语言技术有关的产品"
query_vector = model.encode(query_text, normalize_embeddings=True).tolist()
vector_response = es.search(
    index=INDEX_NAME,
    knn={
        "field": "text_vector",
        "query_vector": query_vector,
        "k": 3,
        "num_candidates": len(documents),
    },
    source_excludes=["text_vector"],
)
vector_hits = compact_hits(vector_response)
print(f"\n[3] 向量检索：{query_text}")
for hit in vector_hits:
    print(hit)

result = {
    "elasticsearch": {
        "version": info["version"]["number"],
        "cluster_name": info["cluster_name"],
        "index": INDEX_NAME,
        "document_count": len(documents),
        "vector_dims": vector_dims,
    },
    "fulltext_search": {"query": "人工智能", "hits": fulltext_hits},
    "conditional_filter": {
        "conditions": "category=电子产品, on_sale=true, price<1000",
        "hits": filter_hits,
    },
    "vector_search": {"query": query_text, "hits": vector_hits},
}
with open(OUTPUT_DIR / "es_results.json", "w", encoding="utf-8") as output:
    json.dump(result, output, ensure_ascii=False, indent=2)

print(f"\n三类检索全部成功，结构化结果已保存到：{OUTPUT_DIR / 'es_results.json'}")
