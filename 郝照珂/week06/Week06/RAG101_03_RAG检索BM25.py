import json
import pdfplumber
import jieba
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize

# 读取数据集
questions = json.load(open("questions.json", encoding="utf-8"))
pdf = pdfplumber.open("汽车知识手册.pdf")
pdf_content = []
for page_idx in range(len(pdf.pages)):
    pdf_content.append({
        'page': 'page_' + str(page_idx + 1),
        'content': pdf.pages[page_idx].extract_text()
    })

# !pip install rank_bm25
from rank_bm25 import BM25Okapi

pdf_content_words = [jieba.lcut(x['content']) for x in pdf_content]
bm25 = BM25Okapi(pdf_content_words)

# 每个提问，与所有页面的bm25打分
for query_idx in range(len(questions)):
    # 只需要对部分的文档进行打分， 部分文档 =》 与提问包含了相同单词的文档 -》 从倒排
    doc_scores = bm25.get_scores(jieba.lcut(questions[query_idx]["question"]))
    max_score_page_idx = doc_scores.argsort()[::-1][0] + 1
    questions[query_idx]['reference'] = 'page_' + str(max_score_page_idx)

with open('submit_bm25_retrieval_top1.json', 'w', encoding='utf8') as up:
    json.dump(questions, up, ensure_ascii=False, indent=4)



for query_idx in range(len(questions)):
    doc_scores = bm25.get_scores(jieba.lcut(questions[query_idx]["question"]))
    max_score_page_idx = doc_scores.argsort()[::-1] + 1
    questions[query_idx]['reference'] = ['page_' + str(x) for x in max_score_page_idx[:10]]

with open('submit_bm25_retrieval_top10.json', 'w', encoding='utf8') as up:
    json.dump(questions, up, ensure_ascii=False, indent=4)

print(f"BM25 检索完成：问题数={len(questions)}，知识库页数={len(pdf_content)}")
print("示例 Top10：", questions[0]['question'], "=>", questions[0]['reference'])
