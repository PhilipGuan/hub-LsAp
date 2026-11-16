# -*- coding: utf-8 -*-
"""RAG 问答服务：检索 + 组装多模态 prompt + 调用 LLM + 组装图文混排结果。"""

from app import llm_client, vector_store
from app.config import RAG_TOP_K


def answer_question(question, doc_ids=None, user_image_data_urls=None, top_k=RAG_TOP_K):
    """多模态 RAG 问答。

    :param question: 用户问题文本
    :param doc_ids:  限定检索范围的文档 id 列表，None 表示检索所有已入库文档
    :param user_image_data_urls: 用户提问时附带的图片（data URL 列表），实现"图问一起送"
    :return: {
        "answer": str,                 # LLM 回答文本
        "context_images": [data_url],  # 检索到、作为依据展示的图片（供前端图文混排展示）
        "sources": [{"doc_id", "text"}]
    }
    """
    hits = vector_store.query(question, top_k=top_k, doc_ids=doc_ids)

    context_text = "\n---\n".join(h["text"] for h in hits) if hits else "（未检索到相关资料）"
    retrieved_image_paths = []
    for h in hits:
        retrieved_image_paths.extend(h["images"])
    # 去重，最多带 4 张图给 LLM，避免 payload 过大
    retrieved_image_paths = list(dict.fromkeys(retrieved_image_paths))[:4]

    answer = llm_client.ask_multimodal(
        question=question,
        context_text=context_text,
        image_paths=retrieved_image_paths,
        user_image_data_urls=user_image_data_urls,
    )

    context_images = [llm_client.image_to_data_url(p) for p in retrieved_image_paths]

    return {
        "answer": answer,
        "context_images": context_images,
        "sources": [{"doc_id": h["doc_id"], "text": h["text"][:200]} for h in hits],
    }
