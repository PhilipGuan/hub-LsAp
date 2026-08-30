"""
Step 2: 最小可运行版 - BGE 文本相似度检索
目标: 给定查询「我今天很开心」，从 3 条候选文本中按相似度排序
预期最相似的应该是: 「我今天心情很不错」
"""
import os
import sys
import torch
import numpy as np

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(PROJECT_DIR, ".cache")
os.environ["HF_HOME"] = os.path.join(CACHE_DIR, "huggingface")
os.environ["TRANSFORMERS_CACHE"] = os.path.join(CACHE_DIR, "transformers")
os.environ["SENTENCE_TRANSFORMERS_HOME"] = os.path.join(CACHE_DIR, "sentence_transformers")
os.makedirs(CACHE_DIR, exist_ok=True)

LOCAL_MODEL_DIR = os.path.join(PROJECT_DIR, "BAAI", "bge-small-zh-v1.5")

QUERY = "我今天很开心"
DOCUMENTS = [
    "我喜欢机器学习",
    "我喜欢深度学习",
    "我今天心情很不错",
]

INSTRUCTION_FOR_RETRIEVAL = "为这个句子生成表示以用于检索相关文章："


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """计算两个向量的余弦相似度，范围 [-1, 1]，越大越相似"""
    dot = float(np.dot(a, b))
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


if __name__ == "__main__":
    print("=" * 60)
    print("[Step 2] BGE 文本检索 - 最小可运行版")
    print("=" * 60)

    if not os.path.isdir(LOCAL_MODEL_DIR):
        print(f"❌ 模型目录不存在: {LOCAL_MODEL_DIR}")
        print("   请先运行: python 01_下载模型.py")
        sys.exit(1)

    # ── 1. 加载 BGE embedding 模型 ──────────────────────────────
    print(f"\n[1/4] 加载 BGE 模型: {LOCAL_MODEL_DIR}")
    from sentence_transformers import SentenceTransformer

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    print(f"      推理设备: {device}")
    model = SentenceTransformer(LOCAL_MODEL_DIR, device=device)
    dim_fn = getattr(model, "get_embedding_dimension", None) or model.get_sentence_embedding_dimension
    dim = dim_fn() if callable(dim_fn) else dim_fn
    print(f"      向量维度: {dim}")

    # ── 2. 为查询生成 embedding（BGE 要求对 query 加 instruction） ─
    print(f"\n[2/4] 对查询『{QUERY}』生成向量")
    query_with_instruction = INSTRUCTION_FOR_RETRIEVAL + QUERY
    query_vec = model.encode(
        query_with_instruction,
        normalize_embeddings=True,
        convert_to_numpy=True
    )
    print(f"      向量 shape: {query_vec.shape}, 前 5 维: {query_vec[:5].round(4)}")

    # ── 3. 对文档库生成 embedding ────────────────────────────────
    print(f"\n[3/4] 对 {len(DOCUMENTS)} 条文档生成向量")
    doc_vecs = model.encode(
        DOCUMENTS,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False
    )
    for i, doc in enumerate(DOCUMENTS):
        print(f"      [{i}] {doc:20s}  ->  shape {doc_vecs[i].shape}")

    # ── 4. 计算相似度并排序输出 ──────────────────────────────────
    print(f"\n[4/4] 计算余弦相似度并排序 (从高到低)")
    print("-" * 60)
    scores = [cosine_similarity(query_vec, dv) for dv in doc_vecs]
    ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)

    print(f"{'Rank':<6}{'Score':<10}{'Document':<25}{'命中?':<8}")
    print("-" * 60)
    for rank, (idx, score) in enumerate(ranked, 1):
        hit = "✅ 预期" if (rank == 1 and idx == 2) else ""
        print(f"{rank:<6}{score:<10.4f}{DOCUMENTS[idx]:<25}{hit:<8}")

    print("-" * 60)
    top_idx = ranked[0][0]
    if top_idx == 2:
        print("\n🎉 测试通过! 最相似文本正确排序为第一名:")
        print(f"   👉 {DOCUMENTS[top_idx]}  (相似度 {ranked[0][1]:.4f})")
    else:
        print(f"\n⚠️  第一名不是预期的文档，请检查 embedding 或 instruction 配置")
