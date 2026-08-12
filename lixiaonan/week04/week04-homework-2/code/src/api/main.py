"""
模块七：FastAPI 主服务
整合 NER、意图识别、混合检索、RAG 生成、对话管理等所有模块
提供 HTTP 接口供前端或其他服务调用
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Dict, Optional
import uvicorn
import time

from src.retrieval.hybrid_retriever import HybridRetriever, KNOWLEDGE_DOCS
from src.rag.generator import RAGGenerator, MockRAGGenerator
from src.dialogue.session_manager import SessionManager
from src.knowledge_graph.kg_builder import MockKnowledgeGraph
from config import DEEPSEEK_API_KEY


# ─────────────────────────────────────────────────────────
# 1. 应用初始化
# ─────────────────────────────────────────────────────────
app = FastAPI(
    title="电商智能问答系统 API",
    description="基于 RAG + 大模型的电商客服智能问答接口",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 全局服务实例（启动时初始化一次）
kg = MockKnowledgeGraph()
retriever = HybridRetriever(documents=KNOWLEDGE_DOCS, kg=kg)
session_manager = SessionManager()

# 根据是否配置 API Key 自动切换真实/Mock 生成器
_use_real_api = DEEPSEEK_API_KEY and not DEEPSEEK_API_KEY.startswith("your_") and not DEEPSEEK_API_KEY.startswith("sk-请")
if _use_real_api:
    generator = RAGGenerator()
    print(f"✓ 使用真实 Deepseek API（model: {generator.model}）")
else:
    generator = MockRAGGenerator()
    print("⚠ 未检测到有效 API Key，使用 Mock 生成器")

# 简单意图规则（替换为 IntentPredictor 加载训练好的模型）
def rule_based_intent(text: str) -> tuple:
    """
    基于关键词的意图识别（演示用）
    实际项目中替换为 IntentPredictor.predict(text)
    """
    rules = [
        (["快递", "物流", "包裹", "发货", "到哪", "到了吗"], "INT_001_物流查询"),
        (["退款", "退货", "退换", "退钱", "申请退"], "INT_002_退换货咨询"),
        (["参数", "规格", "支持", "颜色", "内存", "多少钱", "价格"], "INT_003_商品参数查询"),
        (["优惠", "折扣", "活动", "满减", "券", "打折"], "INT_004_优惠活动查询"),
        (["投诉", "差评", "态度", "不满", "质量问题"], "INT_005_投诉建议"),
    ]
    for keywords, intent in rules:
        if any(kw in text for kw in keywords):
            return intent, 0.9
    return "INT_006_其他", 0.6

def rule_based_ner(text: str) -> Dict:
    """
    基于词典的实体识别（演示用）
    实际项目中替换为 NERPredictor.extract_entities(text)
    """
    brands = ["耐克", "Nike", "苹果", "Apple", "阿迪达斯"]
    products = ["AirMax", "Air Force", "iPhone 15", "运动鞋", "球鞋"]

    entities = {}
    for brand in brands:
        if brand in text:
            entities.setdefault("BRAND", []).append(brand)
    for product in products:
        if product in text:
            entities.setdefault("PRODUCT", []).append(product)
    return entities


# ─────────────────────────────────────────────────────────
# 2. 请求/响应模型
# ─────────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    session_id: str = Field(default="", description="会话ID，为空则自动创建")
    message: str = Field(..., min_length=1, max_length=500, description="用户消息")

class ChatResponse(BaseModel):
    session_id: str
    answer: str
    intent: str
    intent_confidence: float
    entities: Dict[str, List[str]]
    context_count: int              # 使用的知识条数
    latency_ms: float

class HealthResponse(BaseModel):
    status: str
    components: Dict[str, str]
    timestamp: float

class KnowledgeAddRequest(BaseModel):
    doc_id: str
    content: str

class SessionResetRequest(BaseModel):
    session_id: str


# ─────────────────────────────────────────────────────────
# 3. 核心问答接口
# ─────────────────────────────────────────────────────────
@app.post("/chat", response_model=ChatResponse, summary="发送消息，获取智能回复")
async def chat(req: ChatRequest):
    start = time.time()

    # ── 3.1 会话管理 ──────────────────────────────────────
    if req.session_id:
        session = session_manager.get_or_create(req.session_id)
    else:
        session = session_manager.create_session()

    # ── 3.2 查询增强（槽位代词替换）──────────────────────
    enriched_query = session.get_enriched_query(req.message)

    # ── 3.3 意图识别 + NER ────────────────────────────────
    intent, confidence = rule_based_intent(enriched_query)
    entities = rule_based_ner(enriched_query)

    # ── 3.4 话题切换检测 ──────────────────────────────────
    if session.is_topic_switch(intent):
        # 话题切换时清空部分槽位（保留 order_id 等关键槽位）
        preserved = {k: v for k, v in session.slots.items() if k == "order_id"}
        session.slots = preserved

    # ── 3.5 混合检索 ──────────────────────────────────────
    context_docs = retriever.retrieve(
        query=enriched_query,
        intent=intent,
        entities=entities,
        top_k=5,
    )

    # ── 3.6 RAG 答案生成 ──────────────────────────────────
    history = session.get_history_messages(max_turns=4)
    gen_result = generator.generate(
        query=enriched_query,
        context_docs=context_docs,
        history=history,
    )

    answer = gen_result["answer"]
    latency_ms = (time.time() - start) * 1000

    # ── 3.7 保存会话 ──────────────────────────────────────
    session.add_message("user", req.message, intent=intent, entities=entities)
    session.add_message("assistant", answer)
    session_manager.save_session(session)

    return ChatResponse(
        session_id=session.session_id,
        answer=answer,
        intent=intent,
        intent_confidence=round(confidence, 4),
        entities=entities,
        context_count=len(context_docs),
        latency_ms=round(latency_ms, 2),
    )


# ─────────────────────────────────────────────────────────
# 4. 辅助接口
# ─────────────────────────────────────────────────────────
@app.get("/health", response_model=HealthResponse, summary="健康检查")
async def health_check():
    components = {
        "bm25_retriever": "ok",
        "vector_retriever": "ok",
        "knowledge_graph": "ok (mock)",
        "rag_generator": f"{'real - ' + generator.model if _use_real_api else 'mock'}",
        "session_manager": "ok",
    }
    return HealthResponse(
        status="healthy",
        components=components,
        timestamp=time.time(),
    )


@app.post("/session/reset", summary="重置会话（清空对话历史）")
async def reset_session(req: SessionResetRequest):
    session_manager.delete_session(req.session_id)
    new_session = session_manager.create_session()
    return {"message": "会话已重置", "new_session_id": new_session.session_id}


@app.get("/session/{session_id}", summary="查看会话详情")
async def get_session(session_id: str):
    session = session_manager.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在或已过期")
    return session.to_dict()


@app.post("/knowledge/add", summary="动态添加知识文档")
async def add_knowledge(req: KnowledgeAddRequest):
    new_doc = {"id": req.doc_id, "content": req.content}
    KNOWLEDGE_DOCS.append(new_doc)
    # 重建检索索引
    retriever.bm25_retriever._build_index()
    return {"message": f"知识文档 {req.doc_id} 已添加，索引已更新"}


@app.get("/knowledge/list", summary="查看所有知识文档")
async def list_knowledge():
    return {
        "total": len(KNOWLEDGE_DOCS),
        "documents": KNOWLEDGE_DOCS,
    }


@app.get("/intent/analyze", summary="单独分析文本意图和实体")
async def analyze_intent(text: str):
    intent, confidence = rule_based_intent(text)
    entities = rule_based_ner(text)
    return {
        "text": text,
        "intent": intent,
        "confidence": confidence,
        "entities": entities,
    }


# ─────────────────────────────────────────────────────────
# 5. 启动入口
# ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 55)
    print("  电商智能问答系统 启动中...")
    print("=" * 55)
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info",
    )
