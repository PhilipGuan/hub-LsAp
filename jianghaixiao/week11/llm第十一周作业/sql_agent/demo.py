"""作业1 演示脚本。

用法：
    python demo.py            # 需要 API Key，跑 5 个问题
    python demo.py --schema   # 不调模型，只打印数据库 schema
"""
import sys

from openai import OpenAI

from config import OPENAI_API_KEY, OPENAI_BASE_URL, MODEL_NAME, DB_URL
from db_parser import DBParser
from agent import run_sql_agent

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

    client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)

    for q in QUESTIONS:
        print("\n" + "=" * 70)
        print("问题:", q)
        result = run_sql_agent(q, parser, client, MODEL_NAME)

        print("-" * 70)
        print("选中表 :", result["tables"])
        print("SQL    :", result["sql"])
        print("重试   :", result["retries"])
        print("成功   :", result["success"])
        print("耗时   :", result["elapsed_ms"], "ms")
        print("回答   :", result["answer"])
        if result["last_error"]:
            print("错误   :", result["last_error"])


if __name__ == "__main__":
    main()