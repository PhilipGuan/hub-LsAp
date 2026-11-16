from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from uuid import UUID


MAX_DOCUMENT_BYTES = 50 * 1024 * 1024
DOCUMENT_EXTENSIONS = (".pdf", ".doc", ".docx")


class Status(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    DELETING = "deleting"


def document_path(document_id: str) -> Path:
    return Path("documents") / UUID(document_id).hex


@dataclass(frozen=True)
class Document:
    document_id: str
    filename: str
    attempt_id: str
    status: Status
    created_at: str
    retries: int = 0
    failure_stage: str | None = None
    error: str | None = None
    published: bool = False
    retry_history: list[dict[str, str | None]] = field(default_factory=list)


@dataclass(frozen=True)
class Task:
    document_id: str
    file_path: str
    attempt_id: str

    @classmethod
    def for_document(cls, document: Document) -> "Task":
        suffix = Path(document.filename).suffix.lower()
        # Legacy records always used source.pdf, including uploads named ".pdf".
        if suffix not in DOCUMENT_EXTENSIONS:
            suffix = ".pdf"
        return cls(
            document.document_id,
            (document_path(document.document_id) / f"source{suffix}").as_posix(),
            document.attempt_id,
        )
