"""
模块六：对话管理模块
基于 Redis 的多轮对话会话管理，支持上下文追踪与槽位填充
"""

import json
import uuid
import time
from typing import List, Dict, Optional
from dataclasses import dataclass, asdict, field

try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

from config import REDIS_HOST, REDIS_PORT, REDIS_DB, SESSION_TTL


# ─────────────────────────────────────────────────────────
# 1. 数据结构
# ─────────────────────────────────────────────────────────
@dataclass
class Message:
    role: str           # "user" | "assistant"
    content: str
    intent: str = ""    # 用户意图（仅 role=user 时有值）
    entities: Dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict) -> "Message":
        return cls(**d)


@dataclass
class Session:
    """对话会话，包含完整对话历史和槽位状态"""
    session_id: str
    messages: List[Message] = field(default_factory=list)
    slots: Dict[str, str] = field(default_factory=dict)   # 槽位：{"order_id": "xxx", "product": "yyy"}
    current_intent: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def add_message(self, role: str, content: str, intent: str = "", entities: Dict = None):
        msg = Message(role=role, content=content, intent=intent, entities=entities or {})
        self.messages.append(msg)
        self.updated_at = time.time()

        # 更新当前意图（仅从用户消息更新）
        if role == "user" and intent:
            self.current_intent = intent

        # 更新槽位（从实体中提取）
        if entities:
            self._update_slots(entities)

    def _update_slots(self, entities: Dict):
        """将 NER 实体映射到槽位"""
        slot_mapping = {
            "ORDER": "order_id",
            "PRODUCT": "product_name",
            "BRAND": "brand_name",
            "DATE": "date",
            "PRICE": "price",
        }
        for entity_type, slot_name in slot_mapping.items():
            if entity_type in entities and entities[entity_type]:
                self.slots[slot_name] = entities[entity_type][0]  # 取第一个

    def get_history_messages(self, max_turns: int = 4) -> List[Dict]:
        """获取最近 N 轮对话（用于注入 Prompt）"""
        return [
            {"role": msg.role, "content": msg.content}
            for msg in self.messages[-max_turns * 2:]
        ]

    def is_topic_switch(self, new_intent: str) -> bool:
        """检测是否发生话题切换"""
        if not self.current_intent or not new_intent:
            return False
        # 简单规则：意图不同且不是"其他"类意图视为话题切换
        return (
            self.current_intent != new_intent
            and "其他" not in new_intent
            and "其他" not in self.current_intent
        )

    def get_enriched_query(self, query: str) -> str:
        """
        槽位增强查询：将对话历史中的槽位信息注入当前查询
        例：用户问"它多少钱？" → 注入已知商品名 → "耐克AirMax运动鞋多少钱？"
        """
        enriched = query
        # 代词替换
        if any(word in query for word in ["它", "该商品", "这个", "这款"]):
            if "product_name" in self.slots:
                enriched = query.replace("它", self.slots["product_name"])
                enriched = enriched.replace("该商品", self.slots["product_name"])
                enriched = enriched.replace("这个", self.slots["product_name"])
                enriched = enriched.replace("这款", self.slots["product_name"])
        return enriched

    def to_dict(self) -> Dict:
        return {
            "session_id": self.session_id,
            "messages": [m.to_dict() for m in self.messages],
            "slots": self.slots,
            "current_intent": self.current_intent,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "Session":
        session = cls(session_id=d["session_id"])
        session.messages = [Message.from_dict(m) for m in d["messages"]]
        session.slots = d["slots"]
        session.current_intent = d["current_intent"]
        session.created_at = d["created_at"]
        session.updated_at = d["updated_at"]
        return session


# ─────────────────────────────────────────────────────────
# 2. 会话管理器（Redis 存储）
# ─────────────────────────────────────────────────────────
class SessionManager:
    """
    基于 Redis 的会话管理器
    每个 session_id 对应一个 Session 对象，TTL 默认1小时
    """

    def __init__(
        self,
        host: str = REDIS_HOST,
        port: int = REDIS_PORT,
        db: int = REDIS_DB,
        ttl: int = SESSION_TTL,
    ):
        self.ttl = ttl
        if REDIS_AVAILABLE:
            self.redis = redis.Redis(host=host, port=port, db=db, decode_responses=True)
            try:
                self.redis.ping()
                self._use_redis = True
                print(f"✓ Redis 连接成功 {host}:{port}")
            except Exception:
                print("⚠ Redis 不可用，降级为内存存储")
                self._use_redis = False
                self._memory_store: Dict[str, Dict] = {}
        else:
            self._use_redis = False
            self._memory_store: Dict[str, Dict] = {}

    def create_session(self) -> Session:
        """创建新会话，返回 Session 对象"""
        session_id = str(uuid.uuid4())
        session = Session(session_id=session_id)
        self._save(session)
        return session

    def get_session(self, session_id: str) -> Optional[Session]:
        """获取会话，不存在则返回 None"""
        data = self._load(session_id)
        if data is None:
            return None
        return Session.from_dict(data)

    def get_or_create(self, session_id: str) -> Session:
        """获取会话，不存在则创建"""
        session = self.get_session(session_id)
        if session is None:
            session = Session(session_id=session_id)
            self._save(session)
        return session

    def save_session(self, session: Session):
        """保存会话"""
        self._save(session)

    def delete_session(self, session_id: str):
        """删除会话"""
        if self._use_redis:
            self.redis.delete(f"session:{session_id}")
        else:
            self._memory_store.pop(session_id, None)

    def _save(self, session: Session):
        key = f"session:{session.session_id}"
        value = json.dumps(session.to_dict(), ensure_ascii=False)
        if self._use_redis:
            self.redis.setex(key, self.ttl, value)
        else:
            self._memory_store[session.session_id] = session.to_dict()

    def _load(self, session_id: str) -> Optional[Dict]:
        if self._use_redis:
            value = self.redis.get(f"session:{session_id}")
            return json.loads(value) if value else None
        else:
            return self._memory_store.get(session_id)


# ─────────────────────────────────────────────────────────
# Demo
# ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("对话管理模块演示")
    manager = SessionManager()

    # 创建新会话
    session = manager.create_session()
    print(f"\n创建会话：{session.session_id}")

    # 模拟第一轮对话
    session.add_message("user", "我想退一双耐克运动鞋", intent="INT_002_退换货咨询",
                        entities={"BRAND": ["耐克"], "PRODUCT": ["运动鞋"]})
    session.add_message("assistant", "您好！耐克品牌支持7天无理由退换货，请问您的订单号是多少？")

    # 模拟第二轮（代词指代）
    raw_query = "它的价格是多少？"
    enriched = session.get_enriched_query(raw_query)
    print(f"\n原始查询：{raw_query}")
    print(f"槽位增强后：{enriched}")

    # 查看当前槽位
    print(f"\n当前槽位：{session.slots}")
    print(f"当前意图：{session.current_intent}")

    # 获取历史消息
    history = session.get_history_messages()
    print(f"\n对话历史（{len(history)} 条）：")
    for msg in history:
        print(f"  [{msg['role']}] {msg['content']}")

    # 保存会话
    manager.save_session(session)
    print(f"\n✓ 会话已保存")
