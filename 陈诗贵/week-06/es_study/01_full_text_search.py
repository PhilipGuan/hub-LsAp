# -*- coding: utf-8 -*-
"""全文检索：对 content 字段做 IK 分词 + match 查询。"""

import config
from common import get_es, print_results


def full_text_search(es, query, size=5):
    return es.search(
        index=config.INDEX_NAME,
        body={
            "query": {"match": {"content": query}},
            "size": size,
        },
    )


def main():
    es = get_es()
    queries = [
        "前排座椅通风",
        "行车记录仪",
        "三角警示牌 高速 雨雾",
    ]
    for q in queries:
        print_results(full_text_search(es, q), title=f"全文检索：{q}")


if __name__ == "__main__":
    main()
