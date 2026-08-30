from elasticsearch import Elasticsearch
import json
from sentence_transformers import SentenceTransformer


def print_search_results(response):
    print(f"\t找到 {response['hits']['total']['value']} 条文档：")
    for hit in response['hits']['hits']:
        print(f"\t得分：{hit['_score']}，文档内容：{json.dumps(hit['fields'], ensure_ascii=False)}")

print("正在加载 SentenceTransformer 模型...")
model = SentenceTransformer('../models/BAAI/bge-small-zh-v1.5')
print("模型加载完成。")

es_client = Elasticsearch("http://localhost:9200")
# 测试连接
if es_client.ping():
    print("ES连接成功！")
else:
    print("ES连接失败。请检查 Elasticsearch 服务是否运行。")

# 定义索引名称（表）和映射
index_name = "products"
mapping = {
  "settings": {
    "number_of_shards": 1,
    "number_of_replicas": 0
  },
  "mappings": {
    "properties": {
        "product_id": {
            "type": "keyword"
        },
        "name": {
            "type": "text",
            "analyzer": "ik_max_word"
        },
        "description": {
            "type": "text",
            "analyzer": "ik_smart"
        },
        "price": {
            "type": "float"
        },
        "category": {
            "type": "keyword"
        },
        "product_info_vector": {
            "type": "dense_vector",
            "dims": 512,  # 根据模型的输出维度来设置
            "index": True,
            "similarity": "cosine"
        }
    }
  }
}

# 检查索引是否存在，如果不存在则创建
if not es_client.indices.exists(index=index_name):
    es_client.indices.create(index=index_name, body=mapping)
    print(f"ES索引 '{index_name}' 创建成功。")

    documents = [
        {
            "product_id": "A001",
            "name": "智能手机",
            "description": "最新款智能手机，性能强大，拍照清晰，日常使用体验出色。",
            "price": 4999.50,
            "category": "电子产品"
        },
        {
            "product_id": "B002",
            "name": "无线蓝牙耳机",
            "description": "音质卓越，佩戴舒适，日常通勤可用，拥有超长续航时间。",
            "price": 699.00,
            "category": "电子产品"
        },
        {
            "product_id": "C003",
            "name": "智能手表",
            "description": "时尚潮流外观，佩戴舒适，智能体验出色，续航超长。",
            "price": 1999.00,
            "category": "电子产品"
        },
        {
            "product_id": "K011",
            "name": "纯棉休闲T恤",
            "description": "透气亲肤面料，简约时尚，日常通勤百搭，穿着体验出色。",
            "price": 89.90,
            "category": "服饰服装"
        },
        {
            "product_id": "L012",
            "name": "牛仔长裤",
            "description": "版型修身简约，面料耐磨抗皱，上身佩戴舒适，日常穿搭合适。",
            "price": 229.00,
            "category": "服饰服装"
        },
        {
            "product_id": "M013",
            "name": "陶瓷餐具套装",
            "description": "釉面光滑细腻，简约北欧风格，耐高温，家庭日常使用合适。",
            "price": 168.00,
            "category": "家居用品"
        },
        {
            "product_id": "N014",
            "name": "记忆棉枕头",
            "description": "慢回弹护颈设计，面料透气亲肤，贴合颈部，睡眠体验出色。",
            "price": 139.50,
            "category": "家居用品"
        },
        {
            "product_id": "O015",
            "name": "进口坚果礼盒",
            "description": "多种坚果组合，口感酥脆，营养健康，家庭送礼都很合适。",
            "price": 198.00,
            "category": "食品零食"
        },
        {
            "product_id": "P016",
            "name": "冻干水果脆",
            "description": "保留鲜果风味，口感酥脆可口，开袋即食，日常解馋很合适。",
            "price": 32.80,
            "category": "食品零食"
        },
        {
            "product_id": "Q017",
            "name": "保湿护肤面霜",
            "description": "滋润补水效果好，温和亲肤不刺激，修护干燥肌肤，四季使用合适。",
            "price": 256.00,
            "category": "美妆护肤"
        },
        {
            "product_id": "R018",
            "name": "哑光口红",
            "description": "显色饱满，上嘴佩戴舒适，不易拔干，日常妆容百搭好看。",
            "price": 128.00,
            "category": "美妆护肤"
        },
        {
            "product_id": "S019",
            "name": "户外登山背包",
            "description": "大容量防水面料，肩带减负，背负透气，旅行登山体验出色。",
            "price": 369.00,
            "category": "运动户外"
        },
        {
            "product_id": "T020",
            "name": "瑜伽垫",
            "description": "防滑减震，环保亲肤材质，厚度适中，居家运动使用合适。",
            "price": 79.00,
            "category": "运动户外"
        }
    ]

    for doc in documents:
        # 生成向量
        vector = model.encode(doc['name'] + "," + doc['description']).tolist()
        doc['product_info_vector'] = vector
        es_client.index(index=index_name, document=doc)
        print(f"文档已插入: '{doc['name']}，{doc['category']}'")
else:
    print(f"ES索引 '{index_name}' 已经存在。")


print("\n--- 检索 1: 全文检索 ”商品名称“或”商品描述“包含“智能”的商品 ---")
res_1 = es_client.search(
    index=index_name,
    body={
        "query": {
            "bool": {
              "should": [
                {
                  "term": {
                    "name": {
                      "value": "智能"
                    }
                  }
                },
                {
                  "term": {
                    "description": {
                      "value": "智能"
                    }
                  }
                }
              ]
            }
        },
        "fields": ["name", "description"],
        "_source": False  # 不返回整个文档源
    }
)
print_search_results(res_1)

print("\n--- 检索 2: 条件过滤 “运动户外”类目并且价格大于100的商品 ---")
res_2 = es_client.search(
    index=index_name,
    body={
      "query": {
        "bool": {
          "must": {
            "match": {
              "category": "运动户外"
            }
          },
          "filter": {
            "range": {
              "price": {
                "gte": 100
              }
            }
          }
        }
      },
    "fields": ["name", "description", "price"],
    "_source": False  # 不返回整个文档源
    }
)
print_search_results(res_2)

print("\n--- 检索 3: 向量检索  “商品描述”中有类似：“功能强大，电池续航时间长”语义的商品 ---")
query = "功能强大，电池续航时间长"
query_vector =  model.encode(query).tolist()
res_3 = es_client.search(
    index=index_name,
    body={
        "knn": {
            "field": "product_info_vector",
            "query_vector": query_vector,
            "k": 3,
            "num_candidates": 10
        },
        "fields": ["name", "description"],
        "_source": False  # 不返回整个文档源
    }
)
print_search_results(res_3)




