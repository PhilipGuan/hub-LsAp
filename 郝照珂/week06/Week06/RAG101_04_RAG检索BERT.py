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
for page_idx in range(len(pdf.pages)):
    pdf_content.append({
        'page': 'page_' + str(page_idx + 1),
        'content': pdf.pages[page_idx].extract_text()
    })


# BGE （sbert 微调之后的模型）
MODEL_ROOT = Path(__file__).resolve().parents[2] / 'week05' / 'models'
model = SentenceTransformer(str(MODEL_ROOT / 'bge-small-zh-v1.5')) # 使用课程 week05 已下载模型
question_sentences = [x['question'] for x in questions]
pdf_content_sentences = [x['content'] for x in pdf_content]

# 借助bge bert 对文本编码
question_embeddings = model.encode(question_sentences, normalize_embeddings=True)
pdf_embeddings = model.encode(pdf_content_sentences, normalize_embeddings=True)

for query_idx, feat in enumerate(question_embeddings):
    score = feat @ pdf_embeddings.T
    max_score_page_idx = score.argsort()[::-1][0] + 1
    questions[query_idx]['reference'] = 'page_' + str(max_score_page_idx)
    
with open('submit_bge_retrieval_top1.json', 'w', encoding='utf8') as up:
    json.dump(questions, up, ensure_ascii=False, indent=4)


for query_idx, feat in enumerate(question_embeddings):
    score = feat @ pdf_embeddings.T
    max_score_page_idx = score.argsort()[::-1] + 1
    questions[query_idx]['reference'] = ['page_' + str(x) for x in max_score_page_idx[:10]]
    
with open('submit_bge_retrieval_top10.json', 'w', encoding='utf8') as up:
    json.dump(questions, up, ensure_ascii=False, indent=4)

print(f"BGE 页面级检索完成：问题数={len(questions)}，知识库页数={len(pdf_content)}")
print("示例 Top10：", questions[0]['question'], "=>", questions[0]['reference'])


# jinaai
# modelscope download --model jinaai/jina-embeddings-v2-base-zh --local_dir jinaai/jina-embeddings-v2-base-zh
JINA_PATH = MODEL_ROOT / 'jina-embeddings-v2-base-zh'
if not JINA_PATH.exists():
    print("Jina 模型未安装，跳过可选的 Jina 对照实验；BGE 主实验已完成。")
    raise SystemExit(0)
model = SentenceTransformer(str(JINA_PATH))
question_sentences = [x['question'] for x in questions]
pdf_content_sentences = [x['content'] for x in pdf_content]

question_embeddings = model.encode(question_sentences, normalize_embeddings=True)
pdf_embeddings = model.encode(pdf_content_sentences, normalize_embeddings=True)

for query_idx, feat in enumerate(question_embeddings):
    score = feat @ pdf_embeddings.T
    max_score_page_idx = score.argsort()[::-1][0] + 1
    questions[query_idx]['reference'] = 'page_' + str(max_score_page_idx)

with open('submit_jina_retrieval_top1.json', 'w', encoding='utf8') as up:
    json.dump(questions, up, ensure_ascii=False, indent=4)

for query_idx, feat in enumerate(question_embeddings):
    score = feat @ pdf_embeddings.T
    max_score_page_idx = score.argsort()[::-1] + 1
    questions[query_idx]['reference'] = ['page_' + str(x) for x in max_score_page_idx[:10]]

with open('submit_jina_retrieval_top10.json', 'w', encoding='utf8') as up:
    json.dump(questions, up, ensure_ascii=False, indent=4)
