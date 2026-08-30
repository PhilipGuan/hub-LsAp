import json
from pathlib import Path
import pdfplumber
import jieba
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize
from sentence_transformers import SentenceTransformer

# 读取数据集
questions = json.load(open("questions.json", encoding="utf-8"))
pdf = pdfplumber.open("汽车知识手册.pdf")
pdf_content = []

# chunk 块
# chunk 一段文本 -》 细粒度的文本单位，小于一个页面

def split_text_fixed_size(text, chunk_size):
    """
    定长的chunk划分方法
    - chunk_size chunk大小，chunk包含的字符个数
    """
    return [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]


for page_idx in range(len(pdf.pages)):
    text = pdf.pages[page_idx].extract_text()
    for chunk_text in split_text_fixed_size(text, 40):
        pdf_content.append({
            'page': 'page_' + str(page_idx + 1),
            'content': chunk_text
        })

MODEL_PATH = Path(__file__).resolve().parents[2] / 'week05' / 'models' / 'bge-small-zh-v1.5'
model = SentenceTransformer(str(MODEL_PATH))
question_sentences = [x['question'] for x in questions]
pdf_content_sentences = [x['content'] for x in pdf_content]

question_embeddings = model.encode(question_sentences, normalize_embeddings=True, show_progress_bar=True)
pdf_embeddings = model.encode(pdf_content_sentences, normalize_embeddings=True, show_progress_bar=True)

for query_idx, feat in enumerate(question_embeddings):
    # 用户提问 与 每个chunk 计算相似度
    score = feat @ pdf_embeddings.T
    max_score_page_idx = score.argsort()[-1]
    questions[query_idx]['reference'] = pdf_content[max_score_page_idx]['page']

with open('submit_bge_sgement_retrieval_top1.json', 'w', encoding='utf8') as up:
    json.dump(questions, up, ensure_ascii=False, indent=4)


def remove_duplicates(input_list):
    seen = set()
    result = []
    for item in input_list:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result

for query_idx, feat in enumerate(question_embeddings):
    score = feat @ pdf_embeddings.T
    max_score_page_idx = score.argsort()[::-1]
    pages = [pdf_content[x]['page'] for x in max_score_page_idx]
    questions[query_idx]['reference'] = remove_duplicates(pages[:100])[:10]

with open('submit_bge_sgement_retrieval_top10.json', 'w', encoding='utf8') as up:
    json.dump(questions, up, ensure_ascii=False, indent=4)

print(f"BGE 分块检索完成：问题数={len(questions)}，文本块数={len(pdf_content)}")
print("示例 Top10：", questions[0]['question'], "=>", questions[0]['reference'])
