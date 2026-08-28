"""第五周作业1：使用本地 BGE 模型完成语义文本检索（不使用 ES）。"""
import json
from pathlib import Path

import numpy as np
import torch
from sentence_transformers import SentenceTransformer

WEEK05 = Path(__file__).resolve().parents[2]
MODEL_PATH = WEEK05 / "models" / "bge-small-zh-v1.5"
QUERY = "我今天很开心"
DOCUMENTS = [
    "我喜欢机器学习",
    "我喜欢深度学习",
    "我今天心情很不错",
]


def main() -> None:
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = SentenceTransformer(str(MODEL_PATH), device=device)

    # normalize_embeddings=True 后，向量点积就是余弦相似度。
    query_embedding = model.encode(QUERY, normalize_embeddings=True)
    document_embeddings = model.encode(DOCUMENTS, normalize_embeddings=True)
    scores = document_embeddings @ query_embedding
    ranking = np.argsort(-scores)

    result = {
        "model": "BAAI/bge-small-zh-v1.5",
        "device": device,
        "query": QUERY,
        "results": [
            {
                "rank": rank,
                "text": DOCUMENTS[index],
                "cosine_similarity": round(float(scores[index]), 6),
            }
            for rank, index in enumerate(ranking, start=1)
        ],
    }

    print(f"模型: {result['model']} | 设备: {device}")
    print(f"待检索文本: {QUERY}")
    print("检索结果（按余弦相似度降序）:")
    for item in result["results"]:
        print(f"  Top {item['rank']}: {item['text']} | score={item['cosine_similarity']:.6f}")

    output_path = Path(__file__).with_name("retrieval_result.json")
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
