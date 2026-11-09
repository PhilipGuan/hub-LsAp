"""评测脚本：跑 10 个问题，把结果写成 CSV，人工核对正确性。

用法：
    python evaluation.py
"""
import csv

from openai import OpenAI

from config import OPENAI_API_KEY, OPENAI_BASE_URL, MODEL_NAME, DB_URL
from db_parser import DBParser
from agent import run_sql_agent

QUESTIONS = [
    ("计数", "一共有多少位歌手？"),
    ("筛选", "价格最高的 10 首曲目是什么？"),
    ("JOIN", "每个歌手的专辑数量是多少？"),
    ("聚合", "每个国家的消费总额是多少？"),
    ("时间", "2010 年的订单总额是多少？"),
    ("多表", "销量最高的 5 个音乐流派是什么？"),
    ("多表", "哪个员工负责的客户最多？"),
    ("子查询", "消费金额高于平均订单金额的客户有哪些？"),
    ("空结果", "2025 年的订单有多少？"),
    ("易错", "每个播放列表里有多少首歌？"),
]


def main():
    parser = DBParser(DB_URL)
    client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)

    rows = []
    for i, (kind, q) in enumerate(QUESTIONS, 1):
        print(f"[{i}/{len(QUESTIONS)}] {kind}: {q}")
        r = run_sql_agent(q, parser, client, MODEL_NAME, verbose=False)
        rows.append({
            "no": i,
            "type": kind,
            "question": q,
            "tables": ",".join(r["tables"]),
            "sql": r["sql"],
            "retries": r["retries"],
            "success": r["success"],
            "elapsed_ms": r["elapsed_ms"],
            "answer": r["answer"],
            "error": r["last_error"] or "",
            "correct": "",      # 人工填写 ✅/❌
        })

    with open("eval_result.csv", "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    ok = sum(1 for r in rows if r["success"])
    print(f"\n执行成功: {ok}/{len(rows)} = {ok / len(rows):.1%}")
    print("结果已写入 eval_result.csv，请人工核对 correct 列。")


if __name__ == "__main__":
    main()