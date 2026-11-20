"""ORM 模型，对应《需求文档》§4。

- document  文档表：解析状态机 PENDING -> PARSING -> SUCCESS / FAILED（F2-3）
- chunk     分块表：TEXT / IMAGE 两类，含 image_url、page_no、vector_id
- conversation / message 会话表：TODO(M4) 功能3 问答时补充
"""
import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DocumentStatus(str, enum.Enum):
    PENDING = "PENDING"    # 待解析
    PARSING = "PARSING"    # 解析中
    SUCCESS = "SUCCESS"    # 解析完成
    FAILED = "FAILED"      # 解析失败


class ChunkType(str, enum.Enum):
    TEXT = "TEXT"
    IMAGE = "IMAGE"


class Document(Base):
    __tablename__ = "document"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    file_name: Mapped[str] = mapped_column(String(512), comment="原始文件名")
    file_hash: Mapped[str] = mapped_column(String(64), index=True, comment="SHA-256，判重（F1-9）")
    storage_path: Mapped[str] = mapped_column(String(1024), comment="原件存储 key（F1-3）")
    size: Mapped[int] = mapped_column(BigInteger, comment="文件字节数")
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, native_enum=False, length=20,
             values_callable=lambda e: [m.value for m in e]),
        default=DocumentStatus.PENDING, index=True, comment="解析状态",
    )
    fail_reason: Mapped[str | None] = mapped_column(Text, nullable=True, comment="失败原因")
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="PDF 页数")
    parser_name: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="使用的解析器")
    markdown_path: Mapped[str | None] = mapped_column(String(1024), nullable=True, comment="解析产物 Markdown 的存储 key")
    parse_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    parse_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    @property
    def duration_seconds(self) -> float | None:
        """解析耗时（秒），F2-1 展示用。"""
        if self.parse_started_at and self.parse_finished_at:
            return round((self.parse_finished_at - self.parse_started_at).total_seconds(), 2)
        return None


class Chunk(Base):
    __tablename__ = "chunk"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(String(36), ForeignKey("document.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer, comment="块序号（同一文档内递增）")
    chunk_type: Mapped[ChunkType] = mapped_column(
        Enum(ChunkType, native_enum=False, length=10,
             values_callable=lambda e: [m.value for m in e]),
        comment="TEXT / IMAGE",
    )
    content: Mapped[str] = mapped_column(Text, comment="文本内容；IMAGE 类型为图片 URL")
    image_url: Mapped[str | None] = mapped_column(String(1024), nullable=True, comment="图片可访问 URL（IMAGE 类型）")
    page_no: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="所在页码（从 1 开始；无法确定时为空）")
    vector_id: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="向量库中的向量 ID（M4）")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Conversation(Base):
    """会话（F3-7，可选功能）。"""
    __tablename__ = "conversation"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(255), default="新对话")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Message(Base):
    """消息（F3-7）：citations / image_urls 存 JSON 字符串。"""
    __tablename__ = "message"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(String(36), ForeignKey("conversation.id"), index=True)
    role: Mapped[str] = mapped_column(String(20), comment="user / assistant")
    text_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_urls: Mapped[str | None] = mapped_column(Text, nullable=True, comment='JSON 数组，如 ["http://.../a.png"]')
    citations: Mapped[str | None] = mapped_column(Text, nullable=True, comment='JSON 数组，来源引用列表')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
