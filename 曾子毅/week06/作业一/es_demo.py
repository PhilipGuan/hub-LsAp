# -*- coding: utf-8 -*-
import json
import urllib.request
import urllib.error
import sys

ES_URL = "http://localhost:9200"
INDEX  = "demo_shop_py"

MAPPING = {
    "mappings": {
        "properties": {
            "name":      {"type": "text", "analyzer": "standard"},
            "desc":      {"type": "text", "analyzer": "standard"},
            "category":  {"type": "keyword"},
            "price":     {"type": "integer"},
            "rating":    {"type": "float"},
            "tags":      {"type": "keyword"},
            "publish":   {"type": "date"},
            "inStock":   {"type": "boolean"},
            "embedding": {
                "type": "dense_vector",
                "dims": 8,
                "index": True,
                "similarity": "cosine"
            }
        }
    }
}

DOCS = [
  {"_id":"1", "name":"iPhone 15 Pro 苹果手机",
   "desc":"A17 Pro 芯片 专业摄像系统 钛金属设计 5G 智能手机",
   "category":"数码产品","price":7999, "rating":4.8,
   "tags":["5G","摄影","iOS"],"publish":"2024-09-20","inStock":True,
   "embedding":[0.90,0.80,0.10,0.20,0.05,0.30,0.70,0.85]},
  {"_id":"2", "name":"MacBook Pro 14寸 M3",
   "desc":"M3 芯片 16G 内存 512G 存储 专业级笔记本电脑 办公编程",
   "category":"数码产品","price":14999,"rating":4.9,
   "tags":["办公","M3芯片","编程"],"publish":"2024-11-05","inStock":True,
   "embedding":[0.95,0.75,0.08,0.15,0.10,0.25,0.80,0.90]},
  {"_id":"3", "name":"小米手机14 Ultra",
   "desc":"徕卡光学全焦段四摄 骁龙8Gen3 高性能安卓旗舰5G手机",
   "category":"数码产品","price":3999, "rating":4.7,
   "tags":["性价比","5G","安卓"],"publish":"2024-10-15","inStock":True,
   "embedding":[0.85,0.82,0.12,0.25,0.03,0.35,0.65,0.80]},
  {"_id":"4", "name":"深入理解Elasticsearch",
   "desc":"覆盖 ES 9.x 核心原理 分布式架构 分词与检索 向量化搜索 实战书籍",
   "category":"图书","price":89,  "rating":4.6,
   "tags":["技术","ES","搜索引擎"],"publish":"2025-03-10","inStock":True,
   "embedding":[0.20,0.10,0.95,0.88,0.30,0.05,0.10,0.15]},
  {"_id":"5", "name":"百年孤独 50周年纪念版",
   "desc":"加西亚马尔克斯经典魔幻现实主义文学巨著 世界名著小说",
   "category":"图书","price":45,  "rating":4.9,
   "tags":["文学","小说","名著"],"publish":"2023-06-01","inStock":True,
   "embedding":[0.10,0.15,0.88,0.92,0.25,0.10,0.05,0.12]},
  {"_id":"6", "name":"Java编程思想 第5版",
   "desc":"Bruce Eckel 经典 Java 核心编程教程 面向对象 并发编程",
   "category":"图书","price":108, "rating":4.5,
   "tags":["技术","Java","编程"],"publish":"2024-01-18","inStock":False,
   "embedding":[0.22,0.08,0.92,0.85,0.35,0.08,0.12,0.18]},
  {"_id":"7", "name":"三只松鼠坚果大礼包",
   "desc":"每日坚果混合装 碧根果夏威夷果腰果健康零食礼盒1.5kg",
   "category":"食品","price":69,  "rating":4.5,
   "tags":["零食","健康","礼盒"],"publish":"2026-01-12","inStock":True,
   "embedding":[0.10,0.20,0.20,0.15,0.95,0.80,0.10,0.05]},
  {"_id":"8", "name":"五常大米 东北有机稻花香5kg",
   "desc":"核心产区东北五常大米 有机种植 当季新米 软糯香甜十斤装",
   "category":"食品","price":129, "rating":4.7,
   "tags":["主食","有机","东北"],"publish":"2025-12-01","inStock":True,
   "embedding":[0.05,0.15,0.15,0.10,0.92,0.85,0.08,0.03]},
  {"_id":"9", "name":"良品铺子手撕牛肉干",
   "desc":"内蒙原切黄牛腿肉 高蛋白无添加 香辣味休闲肉脯零食 250g",
   "category":"食品","price":39,  "rating":4.4,
   "tags":["零食","高蛋白","香辣"],"publish":"2026-04-20","inStock":True,
   "embedding":[0.08,0.25,0.22,0.18,0.90,0.78,0.12,0.08]},
  {"_id":"10","name":"iPad Air M2 平板",
   "desc":"11英寸 M2 芯片 Liquid 视网膜屏 绘画学习办公平板",
   "category":"数码产品","price":4799,"rating":4.7,
   "tags":["平板","学习","iPadOS"],"publish":"2025-05-18","inStock":True,
   "embedding":[0.88,0.78,0.15,0.20,0.08,0.28,0.70,0.82]},
]

def es(method, path, body=None):
    """发起 HTTP 请求到 ES。返回 (status_code, parsed_json)。"""
    url = f"{ES_URL}{path}"
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json; charset=utf-8"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode("utf-8", errors="ignore")[:500]}
    except Exception as e:
        return None, {"error": str(e)}


def banner(title, char="═"):
    width = 68
    print()
    print(char * width)
    print(f"  {title}")
    print(char * width)

def step1_setup():
    banner("Step 1 / 4  初始化索引 & 批量插入数据 (index: " + INDEX + ")")

    code, _ = es("DELETE", f"/{INDEX}")
    print(f"  [1/3] 删除旧索引         → status {code}"
          + (" (索引不存在，跳过)" if code == 404 else " ✓"))

    code, resp = es("PUT", f"/{INDEX}", MAPPING)
    if code not in (200, 201):
        print(f"  ✗ 创建索引失败: {resp}"); sys.exit(1)
    print(f"  [2/3] 创建带 dense_vector 的新索引 → status {code} ✓")

    lines = []
    for d in DOCS:
        doc = {k: v for k, v in d.items() if k != "_id"}
        lines.append(json.dumps({"index": {"_id": d["_id"]}}, ensure_ascii=False))
        lines.append(json.dumps(doc, ensure_ascii=False))
    ndjson = ("\n".join(lines) + "\n").encode("utf-8")

    url = f"{ES_URL}/{INDEX}/_bulk"
    req = urllib.request.Request(
        url, data=ndjson, method="POST",
        headers={"Content-Type": "application/x-ndjson; charset=utf-8"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        bulk = json.loads(resp.read().decode("utf-8"))

    ok = sum(1 for x in bulk["items"] if "index" in x and 200 <= x["index"].get("status", 0) < 300)
    print(f"  [3/3] Bulk 写入 {ok}/{len(DOCS)} 文档 → took {bulk.get('took')}ms ✓")

    es("POST", f"/{INDEX}/_refresh")
    code, cnt = es("GET", f"/{INDEX}/_count")
    print(f"  ★ 索引文档总数: {cnt.get('count', '?')}")

def print_hits(resp, score_label="Score"):
    hits = resp.get("hits", {}).get("hits", [])
    total = resp.get("hits", {}).get("total", {})
    total_n = total.get("value", total) if isinstance(total, dict) else total
    took = resp.get("took", "?")
    print(f"  命中总数: {total_n}   返回条数: {len(hits)}   耗时: {took}ms")
    print("  " + "─" * 66)
    if not hits:
        print("  (无结果)"); return
    print(f"  {'#':<3} {'ID':<3} {score_label:<8} {'分类':<8} {'价格':<7} {'评分':<6} {'库存'}  名称")
    print("  " + "─" * 66)
    for i, h in enumerate(hits, 1):
        s = h.get("_source", {})
        score = h.get("_score", 0)
        score_str = f"{score:.4f}" if isinstance(score, (int, float)) else str(score)
        stock = "✓" if s.get("inStock") else "✗"
        print(f"  {i:<3} {h.get('_id',''):<3} {score_str:<8} {s.get('category',''):<8}"
              f" ￥{s.get('price',0):<6} {s.get('rating',0):<5}  {stock}   {s.get('name','')[:22]}")
        tags = s.get("tags", [])
        if tags:
            print(f"      └ tags: {tags}")
    print("  " + "─" * 66)

def step2_fulltext():
    banner("Step 2 / 4  全文检索 Full-Text (query: '芯片 手机')")

    query = {
        "size": 5,
        "query": {
            "multi_match": {
                "query": "芯片 手机",
                "fields": ["name", "desc"],
                "minimum_should_match": "75%",
                "type": "most_fields"
            }
        },
        "highlight": {
            "pre_tags":  "【", "post_tags": "】",
            "fields": {"name": {"number_of_fragments": 0},
                       "desc": {"fragment_size": 60, "number_of_fragments": 1}}
        }
    }
    print("  DSL: { multi_match: query='芯片 手机', fields:[name,desc], 75% }")
    print()
    code, resp = es("POST", f"/{INDEX}/_search", query)
    if code != 200 or "error" in resp:
        print("  ✗ 失败:", resp); return
    print_hits(resp)

    # 展示高亮
    hits = resp.get("hits", {}).get("hits", [])
    if hits and hits[0].get("highlight"):
        hl = hits[0]["highlight"]
        joined = []
        for lst in hl.values():
            joined.extend(lst)
        if joined:
            print("  高亮片段: " + " / ".join(joined[:3]))

def step3_filter():
    banner("Step 3 / 4  条件过滤 Bool Filter (图书/数码/食品 + ￥0-100 + 评分≥4.5 + 有货 + 价格升序)")

    query = {
        "size": 10,
        "query": {
            "bool": {
                "filter": [
                    {"terms": {"category": ["数码产品", "图书", "食品"]}},
                    {"range": {"price":   {"gte": 0,  "lte": 100}}},
                    {"range": {"rating":  {"gte": 4.5}}},
                    {"term":  {"inStock": True}}
                ]
            }
        },
        "sort": [{"price": "asc"}]   # 价格 从低到高
    }
    print("  DSL: bool.filter [terms(category) + range(price 0-100) + range(rating≥4.5) + term(inStock=true)]")
    print("       + sort: price asc")
    print()
    code, resp = es("POST", f"/{INDEX}/_search", query)
    if code != 200 or "error" in resp:
        print("  ✗ 失败:", resp); return
    print_hits(resp, score_label="Rank")

def step4_knn():
    banner("Step 4 / 4  向量检索 KNN (查询向量=「数码」原型 · Cosine · Top-5)")

    # "数码" 查询向量原型 → 预期 Top-4 全是 数码产品
    query_vector = [0.90, 0.80, 0.10, 0.20, 0.05, 0.30, 0.70, 0.85]

    # 【正确结构】knn 放顶层；如需过滤，另加 query.bool.filter（兄弟字段）
    body = {
        "knn": {
            "field":           "embedding",
            "k":               5,
            "num_candidates":  10,
            "query_vector":    query_vector
        },
        "_source": ["name", "category", "price", "rating", "tags", "publish", "inStock"]
    }
    print("  DSL (关键结构，knn 必须顶层!):")
    print("    { knn: { field:'embedding', k:5, num_candidates:10,")
    print("            query_vector:[0.90,0.80,0.10,0.20,0.05,0.30,0.70,0.85] } }")
    print(f"  预期结果 → Top-4 应为 数码产品，最后 1 名为最相近异类")
    print()
    code, resp = es("POST", f"/{INDEX}/_search", body)
    if code != 200 or "error" in resp:
        print("  ✗ 失败:", resp); return
    print_hits(resp, score_label="Cosine")


def main():
    print()
    print("=" * 68)
    print("   Elasticsearch 9.x 演示  ·  全文检索 / 条件过滤 / 向量检索")
    print("=" * 68)
    print(f"   ES 地址: {ES_URL}")
    print(f"   索引名:  {INDEX}")
    print(f"   Python:  {sys.version.split()[0]}")

    code, info = es("GET", "/")
    if code != 200:
        print(f"\n  ✗ 无法连接 ES ({ES_URL})：{info}")
        print("     请先启动 ES:  e:\\elasticsearch-9.5.2\\bin\\elasticsearch.bat")
        sys.exit(1)
    print(f"   ES 节点: {info.get('name','?')}   版本: {info.get('version',{}).get('number','?')}")

    step1_setup()
    step2_fulltext()
    step3_filter()
    step4_knn()

    banner("全部完成 ✓", "─")
    print(f"  可直接打开 http://localhost:9200/{INDEX}/_search?pretty 验证\n")


if __name__ == "__main__":
    main()
