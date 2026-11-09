"""作业2 演示：LangChain 版 SQL Agent。

用法：
    python demo.py --schema          # 只看数据库结构（不调模型）
    python demo.py                   # 运行 Agent 版
    python demo.py --pipeline        # 运行确定性 Pipeline 版
"""
import sys

from config import DB_URL
from db_parser import DBParser

QUESTIONS = [
    "一共有多少位歌手？",
    "价格最高的 10 首曲目是什么？",
    "每个歌手的专辑数量是多少？",
    "每个国家的消费总额是多少？",
    "2010 年的订单总额是多少？",
]


def main():
    parser = DBParser(DB_URL)
    print(f"数据库: {DB_URL}")
    print(f"表数量: {len(parser.table_names)}")
    print("表列表:", parser.table_names)

    if "--schema" in sys.argv:
        print("\n" + "=" * 60)
        print(parser.get_schema_text())
        return

    if "--pipeline" in sys.argv:
        from chains import build_chains
        from pipeline_langchain import run_pipeline

        chains = build_chains()
        for q in QUESTIONS:
            print("\n" + "=" * 70)
            print("问题:", q)
            result = run_pipeline(q, parser, chains)
            print("选中表:", result["tables"])
            print("SQL   :", result["sql"])
            print("成功  :", result["success"])
            print("回答  :", result["answer"])
        return

    from agent_langchain import build_sql_agent, run_sql_agent

    agent = build_sql_agent(parser)
    for q in QUESTIONS:
        print("\n" + "=" * 70)
        print("问题:", q)
        result = run_sql_agent(q, agent)
        print("消息数:", len(result["messages"]))
        print("最终回答:", result["messages"][-1].content)
        # TODO: 如果用了 response_format，可以打印 result["structured_response"]


if __name__ == "__main__":
    main()