"""作业2 评测骨架：对比 Agent 版和 Pipeline 版。

用法：
    python evaluation.py
"""
import csv

from config import DB_URL
from db_parser import DBParser

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
    rows = []

    # TODO: 构建两条链路
    # agent = build_sql_agent(parser)
    # chains = build_chains()

    for i, (kind, q) in enumerate(QUESTIONS, 1):
        print(f"[{i}/{len(QUESTIONS)}] {kind}: {q}")
        # TODO: 分别跑 agent 版和 pipeline 版，记录耗时/重试/是否正确
        rows.append({
            "no": i, "type": kind, "question": q,
            "agent_success": "", "agent_ms": "", "agent_correct": "",
            "pipeline_success": "", "pipeline_ms": "", "pipeline_correct": "",
        })

    with open("eval_result.csv", "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    print("已生成 eval_result.csv，请人工填写 correct 列。")


if __name__ == "__main__":
    main()