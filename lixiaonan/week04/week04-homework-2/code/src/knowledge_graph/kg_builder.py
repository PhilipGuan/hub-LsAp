"""
模块三：Neo4j 知识图谱构建与查询
构建电商领域知识图谱，存储商品-品牌-政策等实体关系
"""

try:
    from neo4j import GraphDatabase
    NEO4J_AVAILABLE = True
except ImportError:
    NEO4J_AVAILABLE = False

from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass

from config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD


# ─────────────────────────────────────────────────────────
# 1. 数据结构定义
# ─────────────────────────────────────────────────────────
@dataclass
class Triple:
    """知识图谱三元组：(头实体, 关系, 尾实体)"""
    head: str
    head_type: str      # 实体类型：Brand / Product / Policy / Activity
    relation: str       # 关系类型
    tail: str
    tail_type: str
    properties: Dict = None  # 附加属性


# 示例知识三元组（电商领域）
SAMPLE_TRIPLES = [
    Triple("耐克",    "Brand",   "生产",     "AirMax运动鞋", "Product"),
    Triple("耐克",    "Brand",   "生产",     "Air Force 1", "Product"),
    Triple("耐克",    "Brand",   "适用政策",  "7天无理由退换", "Policy"),
    Triple("AirMax运动鞋", "Product", "价格区间", "500-1500元", "Attribute",
           {"unit": "元"}),
    Triple("AirMax运动鞋", "Product", "支持颜色", "黑色/白色/红色", "Attribute"),
    Triple("Air Force 1", "Product", "价格区间", "600-900元", "Attribute"),
    Triple("苹果",    "Brand",   "生产",     "iPhone 15",  "Product"),
    Triple("苹果",    "Brand",   "适用政策",  "14天无理由退换", "Policy"),
    Triple("iPhone 15", "Product", "支持网络", "5G", "Attribute"),
    Triple("iPhone 15", "Product", "价格区间", "5999-7999元", "Attribute"),
    Triple("双十一活动", "Activity", "折扣力度", "满减300元", "Attribute"),
    Triple("双十一活动", "Activity", "活动时间", "2024-11-01至2024-11-11", "Attribute"),
    Triple("7天无理由退换", "Policy", "适用条件", "商品完好无损", "Attribute"),
    Triple("7天无理由退换", "Policy", "处理时效", "3个工作日内退款", "Attribute"),
]


# ─────────────────────────────────────────────────────────
# 2. Neo4j 知识图谱操作类
# ─────────────────────────────────────────────────────────
class KnowledgeGraphBuilder:
    """
    Neo4j 知识图谱构建与查询
    """

    def __init__(
        self,
        uri: str = NEO4J_URI,
        user: str = NEO4J_USER,
        password: str = NEO4J_PASSWORD,
    ):
        if not NEO4J_AVAILABLE:
            raise ImportError("neo4j 未安装，请运行 pip install neo4j 或使用 MockKnowledgeGraph")
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        self._init_constraints()

    def close(self):
        self.driver.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # ── 初始化约束（唯一索引）──────────────────────────────
    def _init_constraints(self):
        with self.driver.session() as session:
            # 为各实体类型创建唯一约束
            for label in ["Brand", "Product", "Policy", "Activity", "Attribute"]:
                session.run(
                    f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) "
                    f"REQUIRE n.name IS UNIQUE"
                )

    # ── 写入单条三元组 ────────────────────────────────────
    def add_triple(self, triple: Triple):
        cypher = (
            f"MERGE (h:{triple.head_type} {{name: $head}}) "
            f"MERGE (t:{triple.tail_type} {{name: $tail}}) "
            f"MERGE (h)-[r:`{triple.relation}`]->(t) "
        )
        if triple.properties:
            props = ", ".join(f"r.{k} = ${k}" for k in triple.properties)
            cypher += f"SET {props} "

        params = {"head": triple.head, "tail": triple.tail}
        if triple.properties:
            params.update(triple.properties)

        with self.driver.session() as session:
            session.run(cypher, **params)

    # ── 批量写入三元组 ────────────────────────────────────
    def batch_add_triples(self, triples: List[Triple]):
        for triple in triples:
            self.add_triple(triple)
        print(f"✓ 成功写入 {len(triples)} 条三元组到知识图谱")

    # ── 查询：根据实体名查询所有关联知识 ─────────────────
    def query_by_entity(self, entity_name: str) -> List[Dict]:
        """
        查询以 entity_name 为头节点或尾节点的所有三元组

        Returns:
            [{"head": ..., "relation": ..., "tail": ...}, ...]
        """
        cypher = """
            MATCH (h)-[r]->(t)
            WHERE h.name = $name OR t.name = $name
            RETURN h.name AS head, type(r) AS relation, t.name AS tail
            LIMIT 20
        """
        with self.driver.session() as session:
            result = session.run(cypher, name=entity_name)
            return [dict(record) for record in result]

    # ── 查询：根据意图和实体精准查询 ──────────────────────
    def query_for_intent(self, intent: str, entities: Dict[str, List[str]]) -> List[str]:
        """
        根据意图类型和识别的实体，执行针对性的图谱查询

        Returns:
            知识文本列表，用于 RAG 上下文
        """
        results = []

        # 物流查询：查询订单相关政策
        if "物流查询" in intent:
            policy_results = self._query_policy("物流")
            results.extend(policy_results)

        # 退换货咨询：查询退换货政策
        elif "退换货" in intent:
            brands = entities.get("BRAND", [])
            products = entities.get("PRODUCT", [])
            search_targets = brands + products if (brands or products) else ["通用"]
            for target in search_targets:
                rows = self.query_by_entity(target)
                policy_rows = [r for r in rows if "政策" in r.get("relation", "")]
                results.extend(self._format_rows(policy_rows))

        # 商品参数查询
        elif "商品参数" in intent:
            products = entities.get("PRODUCT", []) + entities.get("BRAND", [])
            for product in products:
                rows = self.query_by_entity(product)
                attr_rows = [r for r in rows if r.get("tail_type") == "Attribute"
                             or r.get("relation") in ["价格区间", "支持网络", "支持颜色"]]
                results.extend(self._format_rows(rows))

        # 优惠活动查询
        elif "优惠活动" in intent:
            rows = self._query_activities()
            results.extend(rows)

        # 通用：根据所有识别实体查询
        else:
            all_entities = [e for elist in entities.values() for e in elist]
            for entity in all_entities[:3]:     # 最多查 3 个实体
                rows = self.query_by_entity(entity)
                results.extend(self._format_rows(rows))

        return results[:10]   # 最多返回 10 条

    def _query_policy(self, keyword: str) -> List[str]:
        cypher = """
            MATCH (p:Policy)-[r]->(a)
            WHERE p.name CONTAINS $keyword
            RETURN p.name AS head, type(r) AS relation, a.name AS tail
        """
        with self.driver.session() as session:
            result = session.run(cypher, keyword=keyword)
            rows = [dict(record) for record in result]
        return self._format_rows(rows)

    def _query_activities(self) -> List[str]:
        cypher = """
            MATCH (act:Activity)-[r]->(a)
            RETURN act.name AS head, type(r) AS relation, a.name AS tail
            ORDER BY act.name
        """
        with self.driver.session() as session:
            result = session.run(cypher)
            rows = [dict(record) for record in result]
        return self._format_rows(rows)

    @staticmethod
    def _format_rows(rows: List[Dict]) -> List[str]:
        """将三元组格式化为自然语言片段"""
        return [
            f"{row['head']} {row['relation']} {row['tail']}"
            for row in rows
        ]

    # ── 统计图谱规模 ──────────────────────────────────────
    def get_stats(self) -> Dict:
        with self.driver.session() as session:
            node_count = session.run("MATCH (n) RETURN count(n) AS cnt").single()["cnt"]
            rel_count = session.run("MATCH ()-[r]->() RETURN count(r) AS cnt").single()["cnt"]
        return {"nodes": node_count, "relations": rel_count}


# ─────────────────────────────────────────────────────────
# 3. 知识图谱初始化脚本
# ─────────────────────────────────────────────────────────
def init_knowledge_graph():
    """将示例三元组写入 Neo4j，初始化知识图谱"""
    with KnowledgeGraphBuilder() as kg:
        kg.batch_add_triples(SAMPLE_TRIPLES)
        stats = kg.get_stats()
        print(f"知识图谱统计：节点数={stats['nodes']}，关系数={stats['relations']}")


# ─────────────────────────────────────────────────────────
# 4. Mock 版本（无需 Neo4j，用于单元测试）
# ─────────────────────────────────────────────────────────
class MockKnowledgeGraph:
    """
    不依赖 Neo4j 的内存版知识图谱，用于开发调试
    """

    def __init__(self):
        # 将 SAMPLE_TRIPLES 转为内存索引
        self._index: Dict[str, List[Dict]] = {}
        for t in SAMPLE_TRIPLES:
            for key in [t.head, t.tail]:
                self._index.setdefault(key, [])
                self._index[key].append(
                    {"head": t.head, "relation": t.relation, "tail": t.tail}
                )

    def query_by_entity(self, entity_name: str) -> List[Dict]:
        return self._index.get(entity_name, [])

    def query_for_intent(self, intent: str, entities: Dict[str, List[str]]) -> List[str]:
        results = []
        all_entities = [e for elist in entities.values() for e in elist]
        for entity in all_entities[:3]:
            rows = self.query_by_entity(entity)
            results.extend(
                f"{r['head']} {r['relation']} {r['tail']}" for r in rows
            )
        return results[:10]


if __name__ == "__main__":
    print("使用 MockKnowledgeGraph 演示（无需 Neo4j）")
    kg = MockKnowledgeGraph()

    test_entities = {"BRAND": ["耐克"], "PRODUCT": ["AirMax运动鞋"]}
    print("\n查询 '耐克' 相关知识：")
    for row in kg.query_by_entity("耐克"):
        print(f"  {row['head']} --[{row['relation']}]--> {row['tail']}")

    print("\n意图查询（退换货）：")
    results = kg.query_for_intent("INT_002_退换货咨询", test_entities)
    for r in results:
        print(f"  {r}")
