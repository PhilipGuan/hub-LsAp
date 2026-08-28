# -*- coding: utf-8 -*-
"""条件过滤：bool + filter 的 range 过滤，以及全文检索 + 过滤组合。"""

import config
from common import get_es, print_results


def filter_by_page_range(es, min_page, max_page, size=5):
    return es.search(
        index=config.INDEX_NAME,
        body={
            "query": {
                "bool": {
                    "filter": [
                        {"range": {"page_num": {"gte": min_page, "lte": max_page}}}
                    ]
                }
            },
            "size": size,
        },
    )


def full_text_with_filter(es, query, min_page, max_page, size=5):
    return es.search(
        index=config.INDEX_NAME,
        body={
            "query": {
                "bool": {
                    "must": {"match": {"content": query}},
                    "filter": [
                        {"range": {"page_num": {"gte": min_page, "lte": max_page}}}
                    ],
                }
            },
            "size": size,
        },
    )


def main():
    es = get_es()
    print_results(filter_by_page_range(es, 1, 50), title="条件过滤：页码 1~50")
    print_results(filter_by_page_range(es, 300, 400), title="条件过滤：页码 300~400")
    print_results(
        full_text_with_filter(es, "座椅通风", 110, 120),
        title="全文检索 + 过滤：'座椅通风' 且页码 110~120",
    )


if __name__ == "__main__":
    main()
