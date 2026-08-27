"""汇总第六周作业产物，供截图和提交前检查。"""

import json
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "作业输出"
OUTPUT_DIR.mkdir(exist_ok=True)

checks = [
    ("01 数据集读取", "questions.json", 301),
    ("02 TF-IDF 检索", "submit_tfidf_retrieval_top10.json", 301),
    ("03 BM25 检索", "submit_bm25_retrieval_top10.json", 301),
    ("04 BGE 页面检索", "submit_bge_retrieval_top10.json", 301),
    ("05 BGE 分块检索", "submit_bge_sgement_retrieval_top10.json", 301),
    ("06 二阶段重排序", "submit_fusion_bge+bm25_rerank_retrieval.json", 301),
    ("07 RRF 多路召回", "submit_fusion_bge+bm25_retrieval.json", 301),
    ("08 本地 RAG 问答", "submit_fusion_bge+bm25_rag_answer_ollama.json", 5),
]

summary = []
for step, filename, expected_minimum in checks:
    path = BASE_DIR / filename
    try:
        data = json.load(open(path, encoding="utf-8"))
        item = {
            "step": step,
            "status": "PASS" if len(data) >= expected_minimum else "FAIL",
            "records": len(data),
            "file": filename,
        }
        if data:
            item["example_question"] = data[0].get("question", "")
            item["example_reference"] = data[0].get("reference", "")
            if "answer" in data[0] and data[0]["answer"]:
                item["example_answer"] = data[0]["answer"]
    except Exception as exc:
        item = {"step": step, "status": "FAIL", "records": 0, "file": filename, "error": str(exc)}
    summary.append(item)

with open(OUTPUT_DIR / "rag_summary.json", "w", encoding="utf-8") as output:
    json.dump(summary, output, ensure_ascii=False, indent=2)

print("第六周 RAG 01–08 运行验证")
for item in summary:
    print(f"[{item['status']}] {item['step']}：{item['records']} 条 -> {item['file']}")
print(f"通过：{sum(item['status'] == 'PASS' for item in summary)}/{len(summary)}")

es_path = OUTPUT_DIR / "es_results.json"
es_data = json.load(open(es_path, encoding="utf-8"))
es_pass = all(es_data[name]["hits"] for name in ("fulltext_search", "conditional_filter", "vector_search"))
print(
    f"[{'PASS' if es_pass else 'FAIL'}] Elasticsearch 三类检索："
    f"全文={len(es_data['fulltext_search']['hits'])}，"
    f"过滤={len(es_data['conditional_filter']['hits'])}，"
    f"向量={len(es_data['vector_search']['hits'])}；"
    f"版本={es_data['elasticsearch']['version']}"
)
