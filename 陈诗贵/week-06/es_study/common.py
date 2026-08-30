# -*- coding: utf-8 -*-
"""公共工具：连接 ES、打印检索结果、滑窗分块、读取 PDF。"""

import warnings

import pdfplumber
from elasticsearch import Elasticsearch

import config

# 本地自签名 HTTPS 证书，抑制 verify_certs=False 相关的安全警告
warnings.filterwarnings("ignore", message=".*verify_certs=False.*")
warnings.filterwarnings("ignore", message=".*Unverified HTTPS request.*")


def get_es():
    """创建并校验 ES 客户端连接。"""
    es = Elasticsearch(
        config.ES_URL,
        basic_auth=(config.ES_USER, config.ES_PASSWORD),
        verify_certs=False,
    )
    if not es.ping():
        raise ConnectionError("无法连接 Elasticsearch，请确认服务已启动: " + config.ES_URL)
    return es


def split_text(text, chunk_size=config.CHUNK_SIZE, overlap=config.OVERLAP):
    """滑窗分块：每块 chunk_size 字，相邻块重叠 overlap 字。"""
    if not text:
        return []
    if overlap >= chunk_size:
        raise ValueError("overlap 必须小于 chunk_size")
    step = chunk_size - overlap
    chunks = []
    start = 0
    n = len(text)
    while start < n:
        chunk = text[start:start + chunk_size]
        if chunk:
            chunks.append(chunk)
        if start + chunk_size >= n:
            break
        start += step
    return chunks


def read_pdf_pages(pdf_path):
    """读取 PDF，返回 [{'page': 'page_N', 'page_num': N, 'content': '...'}, ...]"""
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for idx, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            pages.append({
                "page": "page_" + str(idx + 1),
                "page_num": idx + 1,
                "content": text,
            })
    return pages


def print_results(response, title="检索结果", content_len=60):
    """打印 ES 搜索结果。"""
    total = response["hits"]["total"]["value"]
    print(f"\n===== {title} =====")
    print(f"命中 {total} 条")
    for hit in response["hits"]["hits"]:
        src = hit["_source"]
        score = hit.get("_score")
        content = src.get("content", "")
        if score is not None:
            print(f"- 得分 {score:.4f} | {src.get('page')} | {content[:content_len]}")
        else:
            print(f"- {src.get('page')} | {content[:content_len]}")
