import json
import pdfplumber
from pathlib import Path
from transformers import AutoModelForSequenceClassification, AutoTokenizer
import torch
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

# 优先加载课程的 Cross-Encoder；若未提供该大模型，则用现有 BGE 做二阶段语义重排。
MODEL_ROOT = Path(__file__).resolve().parents[2] / 'week05' / 'models'
RERANKER_PATH = MODEL_ROOT / 'bge-reranker-base'
if RERANKER_PATH.exists():
    tokenizer = AutoTokenizer.from_pretrained(str(RERANKER_PATH))
    rerank_model = AutoModelForSequenceClassification.from_pretrained(str(RERANKER_PATH))
    rerank_model.eval()
    fallback_model = None
    rerank_mode = 'BGE Cross-Encoder'
else:
    tokenizer = rerank_model = None
    fallback_model = SentenceTransformer(str(MODEL_ROOT / 'bge-small-zh-v1.5'))
    rerank_mode = 'BGE embedding fallback'
print('重排模式：', rerank_mode)

# 进行召回合并
bge = json.load(open('submit_bge_sgement_retrieval_top10.json', encoding='utf-8')) # bge （sbert）稠密检索的结果，每个提问top10的结果
bm25 = json.load(open('submit_bm25_retrieval_top10.json', encoding='utf-8')) # bm25 稀疏检索的结果，每个提问top的结果

fusion_result = []
k = 60 # 超参数，实现发现60
for q1, q2 in zip(bge, bm25):
    # q1 每个提问 的 bge 检索的top10
    # q2 每个提问 的 bm25 检索的top10
    if len(fusion_result) % 50 == 0:
        print(f"重排进度：{len(fusion_result)}/{len(bge)}")

    # 多路检索的结果的合并
    fusion_score = {} # 每个页面 在不同检索方式下的累加的排序的打分
    # key 是页面，value 是打分

    for idx, q in enumerate(q1['reference']):
        if q not in fusion_score:
            fusion_score[q] = 1 / (idx + k) # 排在后面，得分更低
        else:
            fusion_score[q] += 1 / (idx + k)

    for idx, q in enumerate(q2['reference']):
        if q not in fusion_score:
            fusion_score[q] = 1 / (idx + k)
        else:
            fusion_score[q] += 1 / (idx + k)

    # 按照打分对页面排序
    sorted_dict = sorted(fusion_score.items(), key=lambda item: item[1], reverse=True)

    pairs = []
    for sorted_result in sorted_dict[:3]:
        page_index = int(sorted_result[0].split('_')[1]) - 1
        # 新的样本： 【提问， 检索到的文档页面】
        pairs.append([q1["question"], pdf_content[page_index]['content']])


    if rerank_model is not None:
        inputs = tokenizer(pairs, padding=True, truncation=True, return_tensors='pt', max_length=512)
        with torch.no_grad():
            scores = rerank_model(**inputs, return_dict=True).logits.view(-1, ).float().cpu().numpy()
    else:
        query_embedding = fallback_model.encode([q1['question']], normalize_embeddings=True)[0]
        document_embeddings = fallback_model.encode([pair[1] for pair in pairs], normalize_embeddings=True)
        scores = document_embeddings @ query_embedding

    # .cpu 数据 从gpu 移动到cpu进行计算
    sorted_result = sorted_dict[int(scores.argmax())] # 重排模型在 top3 中找到最相关结果
    q1['reference'] = sorted_result[0]

    fusion_result.append(q1)

with open('submit_fusion_bge+bm25_rerank_retrieval.json', 'w', encoding='utf8') as up:
    json.dump(fusion_result, up, ensure_ascii=False, indent=4)

print(f"重排序完成：模式={rerank_mode}，问题数={len(fusion_result)}")
print("示例重排结果：", fusion_result[0]['question'], "=>", fusion_result[0]['reference'])
