from elasticsearch import Elasticsearch
import random
import json
# 连接到本地 ES（无认证）
es = Elasticsearch("http://localhost:9200")

# 检查连接
print(es.info())

index_name = "products"

mapping = {
    "mappings": {
        "properties": {
            "title": {"type": "text"},
            "category": {"type": "keyword"},
            "price": {"type": "float"},
            "embedding": {
                "type": "dense_vector",
                "dims": 3,          # 向量维度
                "index": True,      # 启用索引以支持 kNN 搜索
                "similarity": "cosine"  # 相似度度量，可选 cosine, dot_product, l2norm
            }
        }
    }
}

# 如果索引不存在则创建
if not es.indices.exists(index=index_name):
    es.indices.create(index=index_name, body=mapping)
    print(f"索引 {index_name} 创建成功")
else:
    print(f"索引 {index_name} 已存在")

with open("docs.json", "r",encoding='utf-8') as file:
    docs = json.load(file)

for i, doc in enumerate(docs):
    es.index(index=index_name, id=i+1, document=doc)

# 刷新索引以确保数据可搜索
es.indices.refresh(index=index_name)
print("示例文档已索引")

# 定义检索函数（与之前相同）
def full_text_search(query_text):
    response = es.search(
        index=index_name,
        query={"match": {"title": query_text}}
    )
    return response["hits"]["hits"]

def filtered_search(category=None, min_price=None, max_price=None):
    must_filters = []
    if category:
        must_filters.append({"term": {"category": category}})
    if min_price is not None or max_price is not None:
        price_range = {}
        if min_price is not None:
            price_range["gte"] = min_price
        if max_price is not None:
            price_range["lte"] = max_price
        must_filters.append({"range": {"price": price_range}})

    query = {"bool": {"filter": must_filters}}
    response = es.search(index=index_name, query=query)
    return response["hits"]["hits"]

def vector_search(query_vector, k=3):
    response = es.search(
        index=index_name,
        knn={
            "field": "embedding",
            "query_vector": query_vector,
            "k": k,
            "num_candidates": 10
        }
    )
    return response["hits"]["hits"]

def combined_search(query_text, category, query_vector, k=3):
    script_query = {
        "script_score": {
            "query": {
                "bool": {
                    "must": [
                        {"match": {"title": query_text}},
                        {"term": {"category": category}}
                    ]
                }
            },
            "script": {
                "source": "cosineSimilarity(params.query_vector, 'embedding') + 1.0",
                "params": {"query_vector": query_vector}
            }
        }
    }
    response = es.search(index=index_name, query=script_query, size=k)
    return response["hits"]["hits"]

def print_results(results):
    if not results:
        print("没有找到匹配的文档。")
        return
    for hit in results:
        source = hit["_source"]
        score = hit["_score"]
        print(f"标题: {source['title']}, 类别: {source['category']}, 价格: {source['price']}, 得分: {score}")

# 主交互循环
def main():
    print("=" * 50)
    print("Elasticsearch 检索测试")
    print("=" * 50)
    print("请选择要测试的功能：")
    print("1. 全文检索")
    print("2. 条件过滤")
    print("3. 向量检索")
    print("4. 组合检索（全文 + 过滤 + 向量）")
    print("5. 退出")

    while True:
        choice = input("\n请输入选项 (1-5): ").strip()

        if choice == '1':
            # 全文检索
            keyword = input("请输入搜索关键词: ").strip()
            if not keyword:
                print("关键词不能为空。")
                continue
            results = full_text_search(keyword)
            print("\n全文检索结果：")
            print_results(results)

        elif choice == '2':
            # 条件过滤
            print("\n条件过滤（直接回车表示忽略该条件）")
            category = input("类别 (如 electronics/shoes): ").strip()
            min_price_str = input("最低价格: ").strip()
            max_price_str = input("最高价格: ").strip()

            min_price = float(min_price_str) if min_price_str else None
            max_price = float(max_price_str) if max_price_str else None

            results = filtered_search(category=category or None,
                                      min_price=min_price,
                                      max_price=max_price)
            print("\n条件过滤结果：")
            print_results(results)

        elif choice == '3':
            # 向量检索
            print("\n向量检索（需要输入 3 维向量，用逗号分隔）")
            vec_str = input("查询向量 (如 0.1,0.2,0.9): ").strip()
            try:
                parts = vec_str.split(',')
                query_vec = [float(p.strip()) for p in parts]
                if len(query_vec) != 3:
                    print("向量维度必须为 3。")
                    continue
            except ValueError:
                print("输入格式错误，请输入数字，用逗号分隔。")
                continue

            k_str = input("返回结果数量 k (默认 3): ").strip()
            k = int(k_str) if k_str else 3

            results = vector_search(query_vec, k=k)
            print("\n向量检索结果：")
            print_results(results)

        elif choice == '4':
            # 组合检索
            print("\n组合检索")
            keyword = input("搜索关键词: ").strip()
            category = input("类别 (如 electronics): ").strip()
            print("查询向量（3 维，逗号分隔）")
            vec_str = input("如 0.1,0.2,0.9: ").strip()
            try:
                parts = vec_str.split(',')
                query_vec = [float(p.strip()) for p in parts]
                if len(query_vec) != 3:
                    print("向量维度必须为 3。")
                    continue
            except ValueError:
                print("输入格式错误。")
                continue

            k_str = input("返回结果数量 k (默认 3): ").strip()
            k = int(k_str) if k_str else 3

            results = combined_search(keyword, category, query_vec, k=k)
            print("\n组合检索结果：")
            print_results(results)

        elif choice == '5':
            print("退出测试。")
            break

        else:
            print("无效选项，请输入 1-5。")

if __name__ == "__main__":
    main()