"""Kafka 消息结构定义（§3.1.3）：pdf-parse-task Topic 的消息体。"""
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from app.utils.file_utils import new_id


class ParseTaskMessage(BaseModel):
    """解析任务消息。Worker 消费成功后手动提交 offset；失败按 retry_count 重试（F1-6）。"""

    task_id: str = Field(default_factory=new_id)
    file_id: str
    file_name: str
    storage_path: str          # 原件存储 key（local: uploads/<id>.pdf；minio: 对象 key）
    file_hash: str
    retry_count: int = 0
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_bytes(self) -> bytes:
        return self.model_dump_json().encode("utf-8")

    @classmethod
    def from_bytes(cls, raw: bytes) -> "ParseTaskMessage":
        return cls.model_validate_json(raw)
