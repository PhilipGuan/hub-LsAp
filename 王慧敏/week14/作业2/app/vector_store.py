# -*- coding: utf-8 -*-
"""向量库封装（Chroma，本地持久化，零外部依赖）。

多模态检索策略（实用做法，非学术最优）：
    - MinerU 解析出的 markdown 是"文字 + 图片引用"混排的
    - 按标题/段落切分成 chunk 时，保留该 chunk 内出现的图片路径到 metadata.images
    - 检索时用纯文本 embedding 找最相关的 chunk，chunk 自带的图片路径就是
      "该文字段落配套的图"，从而间接实现"文字→图"的图文关联检索
    - embedding 模型用 sentence-transformers 本地小模型，不依赖外部 API
"""

import os
import re

import chromadb
from sentence_transformers import SentenceTransformer

from app.config import CHROMA_DIR, CHUNK_OVERLAP, CHUNK_SIZE, EMBEDDING_MODEL_NAME

_client = None
_collection = None
_embedder = None


def _get_embedder():
    global _embedder
    if _embedder is None:
        _embedder = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return _embedder


def _get_collection():
    global _client, _collection
    if _collection is None:
        _client = chromadb.PersistentClient(path=CHROMA_DIR)
        _collection = _client.get_or_create_collection(name="pdf_chunks")
    return _collection


def split_markdown_with_images(markdown_text, md_dir):
    """把 markdown 切分为文本块，每块附带块内引用的图片绝对路径列表。

    切分规则：先按图片引用位置打断，再对纯文本部分做定长滑窗切分，
    保证每个图片都单独成块（配套紧邻的上下文文本），不会被切散。
    """
    img_pattern = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
    chunks = []  # list of {"text": str, "images": [abs_path, ...]}

    pos = 0
    pending_text = ""
    for m in img_pattern.finditer(markdown_text):
        pending_text += markdown_text[pos:m.start()]
        img_ref = m.group(1).split("?")[0]
        abs_img = os.path.normpath(os.path.join(md_dir, img_ref))
        # 把图片前面积累的文本 + 这张图打包成一个块（图文配对）
        context = pending_text.strip()[-CHUNK_SIZE:] if pending_text.strip() else ""
        chunks.append({"text": context or "（图片）", "images": [abs_img] if os.path.isfile(abs_img) else []})
        pending_text = ""
        pos = m.end()
    pending_text += markdown_text[pos:]

    # 剩余纯文本部分再做定长滑窗切分
    for chunk_text in _sliding_window(pending_text.strip(), CHUNK_SIZE, CHUNK_OVERLAP):
        if chunk_text.strip():
            chunks.append({"text": chunk_text, "images": []})

    return [c for c in chunks if c["text"].strip()]


def _sliding_window(text, size, overlap):
    if not text:
        return []
    step = max(size - overlap, 1)
    return [text[i:i + size] for i in range(0, len(text), step)]


def add_document_chunks(doc_id, chunks):
    """把一个文档的所有 chunk 写入向量库。

    :param chunks: [{"text": str, "images": [abs_path, ...]}, ...]
    :return: 实际写入的 chunk 数
    """
    if not chunks:
        return 0
    collection = _get_collection()
    embedder = _get_embedder()
    texts = [c["text"] for c in chunks]
    embeddings = embedder.encode(texts, normalize_embeddings=True).tolist()

    ids = [f"{doc_id}:{i}" for i in range(len(chunks))]
    metadatas = [
        {"doc_id": doc_id, "images": "||".join(c["images"]) if c["images"] else ""}
        for c in chunks
    ]
    collection.add(ids=ids, embeddings=embeddings, documents=texts, metadatas=metadatas)
    return len(chunks)


def query(question, top_k=5, doc_ids=None):
    """检索与问题最相关的文本块，返回 [{"text", "images": [...], "doc_id"}, ...]。"""
    collection = _get_collection()
    embedder = _get_embedder()
    query_embedding = embedder.encode([question], normalize_embeddings=True).tolist()

    where = {"doc_id": {"$in": doc_ids}} if doc_ids else None
    result = collection.query(
        query_embeddings=query_embedding,
        n_results=top_k,
        where=where,
    )

    hits = []
    docs = result.get("documents", [[]])[0]
    metas = result.get("metadatas", [[]])[0]
    for text, meta in zip(docs, metas):
        images = [p for p in (meta.get("images") or "").split("||") if p]
        hits.append({"text": text, "images": images, "doc_id": meta.get("doc_id")})
    return hits


def count_images(chunks):
    return sum(len(c["images"]) for c in chunks)
