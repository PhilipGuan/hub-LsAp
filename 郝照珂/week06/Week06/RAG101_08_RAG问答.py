"""使用本机 Ollama 完成离线 RAG 问答，避免在源码中保存第三方 API 密钥。"""

import json
import os

import pdfplumber
import requests


OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:0.6b")
MAX_QUESTIONS = int(os.getenv("RAG_MAX_QUESTIONS", "5"))  # 设为 0 可运行全部 301 条


def ask_local_llm(question: str, reference: str, page_name: str) -> str:
    pdf_page = page_name.split("_")[-1]
    prompt = f"""/no_think
你是汽车知识助手。只能根据给定资料，用简洁中文回答问题；资料不足时回答“根据资料无法回答”。如果问题询问页码，必须回答“资料来源”给出的 PDF 页码，不要使用正文页眉页脚中的数字。

资料来源：PDF 第 {pdf_page} 页。
资料正文：{reference.replace(chr(10), ' ')}

问题：{question}
"""
    response = requests.post(
        f"{OLLAMA_URL}/api/chat",
        json={
            "model": OLLAMA_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "think": False,
            "options": {"temperature": 0.1, "num_predict": 256},
        },
        timeout=120,
    )
    response.raise_for_status()
    answer = response.json()["message"]["content"].strip()
    return answer or "根据资料无法回答"


bge = json.load(open("submit_bge_sgement_retrieval_top10.json", encoding="utf-8"))
bm25 = json.load(open("submit_bm25_retrieval_top10.json", encoding="utf-8"))

with pdfplumber.open("汽车知识手册.pdf") as pdf:
    pdf_content = {
        f"page_{page_idx + 1}": page.extract_text() or ""
        for page_idx, page in enumerate(pdf.pages)
    }

limit = len(bge) if MAX_QUESTIONS == 0 else min(MAX_QUESTIONS, len(bge))
fusion_result = []
k = 60
print(f"本地问答模型：{OLLAMA_MODEL}；本次演示问题数：{limit}")

for index, (dense_item, sparse_item) in enumerate(zip(bge[:limit], bm25[:limit]), 1):
    fusion_score = {}
    for retrieval_item in (dense_item, sparse_item):
        for rank, page_name in enumerate(retrieval_item["reference"]):
            fusion_score[page_name] = fusion_score.get(page_name, 0.0) + 1 / (rank + k)

    best_page = max(fusion_score, key=fusion_score.get)
    reference = pdf_content[best_page]
    try:
        answer = ask_local_llm(dense_item["question"], reference, best_page)
    except Exception as exc:
        answer = "本地模型调用失败；检索到的原文摘要：" + reference.replace("\n", " ")[:220]
        print(f"第 {index} 条调用失败：{exc}")

    result_item = dict(dense_item)
    result_item["reference"] = best_page
    result_item["answer"] = answer
    fusion_result.append(result_item)

    print(f"[{index}/{limit}] 用户提问：{result_item['question']}")
    print(f"检索页码：{best_page}")
    print(f"模型回答：{answer}\n")

with open("submit_fusion_bge+bm25_rag_answer_ollama.json", "w", encoding="utf-8") as output:
    json.dump(fusion_result, output, ensure_ascii=False, indent=4)

print(f"RAG 问答完成：生成 {len(fusion_result)} 条答案。")
