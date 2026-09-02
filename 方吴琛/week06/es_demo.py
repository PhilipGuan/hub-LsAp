"""
本地配置下es，基于es完成下全文检索、条件过滤、向量检索，截图。
"""
import json
import time
from elasticsearch import Elasticsearch
from sentence_transformers import SentenceTransformer

INDEX_NAME = "student_info"

# 替换为你的 Elasticsearch 地址
es = Elasticsearch("http://loaclhost:9200")

model = SentenceTransformer('../models/BAAI/bge-small-zh-v1.5')


if es.ping():
    print("成功连接到 Elasticsearch！")
else:
    print("无法连接到 Elasticsearch，请检查服务是否运行。")


def prepare_data():
    # create
    if es.indices.exists(index=INDEX_NAME):
        es.indices.delete(index=INDEX_NAME)
        print(f"旧索引 {INDEX_NAME} 已删除")
    mappings = {
        "mappings": {
            "properties": {
                "name": {"type": "keyword"},
                "age": {"type": "integer"},
                "major": {
                    "type": "text",
                    "analyzer": "ik_max_word",
                    "search_analyzer": "ik_smart"
                },
                "address": {
                    "type": "text",
                    "analyzer": "ik_max_word",
                    "search_analyzer": "ik_smart"
                }
            }
        }
    }
    es.indices.create(index=INDEX_NAME, body=mappings)

    # insert
    student_list = [
        {
            "_id": 1,
            "name": "张三",
            "age": 20,
            "major": "计算机科学与技术",
            "address": "浙江省杭州市西湖区"
        },
        {
            "_id": 2,
            "name": "李四",
            "age": 22,
            "major": "人工智能大模型方向",
            "address": "上海市浦东新区张江"
        }
    ]

    for stu in student_list:
        doc_id = stu.pop("_id")
        es.index(index=INDEX_NAME, id=doc_id, document=stu)

    es.indices.refresh(index=INDEX_NAME)


def print_search_results(response):
    print(f"找到 {response['hits']['total']['value']} 条文档：")
    for hit in response['hits']['hits']:
        print(f"得分：{hit['_score']}，文档内容：{json.dumps(hit['_source'], ensure_ascii=False, indent=2)}")


def print_semantic_search_results(response):
    for hit in response['hits']['hits']:
        score = hit['_score']
        text = hit['fields']['text'][0]
        print(f"得分: {score:.4f}, 内容: {text}")


def es_match():
    print("--- 全文检索 ---")

    keyword = "杭州"
    body = {
        "query": {
            "multi_match": {
                "query": keyword,
                "fields": ["major", "address"]
            }
        }
    }
    res = es.search(
        index=INDEX_NAME,
        body=body
    )
    print_search_results(res)


def es_filter():
    print("--- 查询过滤 ---")
    query = {
        "query": {
            "match": {
                "name": "李四"
            }
        }
    }
    res = es.search(
        index=INDEX_NAME,
        body=query
    )
    print_search_results(res)


def es_semantic_search():
    print("--- 执行向量检索 ---")
    index_name = "semantic_search_demo"
    if es.indices.exists(index=index_name):
        es.indices.delete(index=index_name)
        print(f"旧索引 '{index_name}' 已删除。")

    es.indices.create(
        index=index_name,
        body={
            "mappings": {
                "properties": {
                    "text": {"type": "text"},
                    "text_vector": {
                        "type": "dense_vector",
                        "dims": 512,  # 根据模型的输出维度来设置
                        "index": True,
                        "similarity": "cosine"
                    }
                }
            }
        }
    )
    documents = [
        "人工智能是未来的趋势。",
        "机器学习是人工智能的一个重要分支。",
        "自然语言处理技术让机器理解人类语言。",
        "今天天气真好，适合出去玩。",
        "工程技术需要画图。",
        "我最喜欢的运动是篮球和足球。"
    ]

    for doc_text in documents:
        # 生成向量
        vector = model.encode(doc_text).tolist()

        # 插入文档
        es.index(
            index=index_name,
            document={
                "text": doc_text,
                "text_vector": vector
            }
        )

    es.indices.refresh(index=index_name)
    time.sleep(1)  # 等待索引刷新

    query_text = "关于AI和未来的技术"

    # 将查询文本转换为向量
    query_vector = model.encode(query_text).tolist()

    # 使用 knn 查询进行向量检索
    res = es.search(
        index=index_name,
        body={
            "knn": {
                "field": "text_vector",
                "query_vector": query_vector,
                "k": 3,
                "num_candidates": 10
            },
            "fields": ["text"],  # 返回 text 字段
            "_source": False  # 不返回整个文档源
        }
    )
    print_semantic_search_results(res)


if __name__ == '__main__':
    prepare_data()
    es_match()
    es_filter()
    es_semantic_search()
