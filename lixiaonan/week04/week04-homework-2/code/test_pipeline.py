"""
端到端集成测试脚本
验证所有模块的联动是否正常（无需真实 API / Neo4j / Redis）
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from src.knowledge_graph.kg_builder import MockKnowledgeGraph
from src.retrieval.hybrid_retriever import HybridRetriever, KNOWLEDGE_DOCS
from src.rag.generator import MockRAGGenerator
from src.dialogue.session_manager import SessionManager


def run_test(query: str, retriever, generator, session):
    """执行单轮问答流程"""
    # 简单意图规则
    intent_rules = [
        (["快递", "物流", "包裹", "到了"], "INT_001_物流查询"),
        (["退款", "退货", "退换"], "INT_002_退换货咨询"),
        (["价格", "多少钱", "5G", "颜色"], "INT_003_商品参数查询"),
        (["优惠", "折扣", "活动"], "INT_004_优惠活动查询"),
        (["投诉", "差评", "不满"], "INT_005_投诉建议"),
    ]
    intent = "INT_006_其他"
    for keywords, i in intent_rules:
        if any(kw in query for kw in keywords):
            intent = i
            break

    # NER（简单词典）
    entities = {}
    for brand in ["耐克", "苹果", "Apple", "Nike"]:
        if brand in query:
            entities.setdefault("BRAND", []).append(brand)
    for product in ["AirMax", "iPhone 15", "运动鞋"]:
        if product in query:
            entities.setdefault("PRODUCT", []).append(product)

    # 查询增强
    enriched = session.get_enriched_query(query)

    # 混合检索
    docs = retriever.retrieve(query=enriched, intent=intent, entities=entities)

    # RAG 生成
    history = session.get_history_messages()
    result = generator.generate(query=enriched, context_docs=docs, history=history)

    # 更新会话
    session.add_message("user", query, intent=intent, entities=entities)
    session.add_message("assistant", result["answer"])

    return {
        "query": query,
        "enriched_query": enriched,
        "intent": intent,
        "entities": entities,
        "context_count": len(docs),
        "answer": result["answer"],
    }


def main():
    print("=" * 60)
    print("  电商智能问答系统 — 集成测试")
    print("=" * 60)

    # 初始化所有模块
    print("\n[1/4] 初始化知识图谱 (Mock) ...")
    kg = MockKnowledgeGraph()

    print("[2/4] 初始化混合检索器 ...")
    retriever = HybridRetriever(documents=KNOWLEDGE_DOCS, kg=kg)

    print("[3/4] 初始化 RAG 生成器 (Mock) ...")
    generator = MockRAGGenerator()

    print("[4/4] 初始化会话管理器 ...")
    session_manager = SessionManager()
    session = session_manager.create_session()
    print(f"  会话 ID: {session.session_id}\n")

    # 测试用例
    test_cases = [
        "我的包裹到哪了？",
        "我想退一双耐克运动鞋，怎么操作？",
        "它支持7天退换吗？",          # 代词指代测试
        "iPhone 15 支持5G吗？",
        "双十一有什么优惠活动？",
        "客服态度太差了，要投诉！",
    ]

    print("=" * 60)
    for i, query in enumerate(test_cases, 1):
        result = run_test(query, retriever, generator, session)
        print(f"\n[测试 {i}]")
        print(f"  用户：{result['query']}")
        if result['enriched_query'] != result['query']:
            print(f"  增强：{result['enriched_query']}")
        print(f"  意图：{result['intent']}")
        if result['entities']:
            print(f"  实体：{result['entities']}")
        print(f"  检索：{result['context_count']} 条知识")
        print(f"  回复：{result['answer']}")

    print("\n" + "=" * 60)
    print(f"测试完成！共测试 {len(test_cases)} 条问题")
    print(f"会话历史消息数：{len(session.messages)}")
    print(f"最终槽位状态：{session.slots}")

    session_manager.save_session(session)
    print("✓ 会话已保存")


if __name__ == "__main__":
    main()
