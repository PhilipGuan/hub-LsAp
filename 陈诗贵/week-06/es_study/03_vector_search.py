# -*- coding: utf-8 -*-
"""向量检索：把 query 编码成向量，用 knn 做语义检索。"""

from sentence_transformers import SentenceTransformer

import config
from common import get_es, print_results


def vector_search(es, model, query, k=5):
    query_vec = model.encode([query], normalize_embeddings=True)[0].tolist()
    return es.search(
        index=config.INDEX_NAME,
        body={
            "knn": {
                "field": "content_vector",
                "query_vector": query_vec,
                "k": k,
                "num_candidates": 100,
            },
            "size": k,
        },
    )


def main():
    es = get_es()
    model = SentenceTransformer(config.MODEL_PATH)

    queries = [
        "座椅怎么通风",
        "怎么打开前机舱盖",
        "儿童安全座椅怎么固定",
    ]
    for q in queries:
        print_results(vector_search(es, model, q, k=3), title=f"向量检索：{q}")


if __name__ == "__main__":
    main()
