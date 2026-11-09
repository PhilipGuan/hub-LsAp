"""后台研究任务：创建、执行、逐步落盘和完成。"""
from __future__ import annotations

from datetime import datetime, timezone

from . import storage
from .engine import DeepResearch
from .models import (
    ConfidenceNote,
    ReportSection,
    ResearchProcess,
    Source,
    Status,
)


async def run_research(research_id: str, topic: str) -> None:
    try:
        storage.update_status(research_id, Status.running)

        async def on_progress(snapshot: dict) -> None:
            record = storage.get(research_id)
            if record is None:
                return
            record.process = ResearchProcess.model_validate(snapshot["process"])
            record.draft = [
                ReportSection.model_validate(item) for item in snapshot["draft"]
            ]
            record.sources = [
                Source.model_validate(item) for item in snapshot["sources"]
            ]
            record.updated_at = datetime.now(timezone.utc).isoformat()
            storage.save(record)

        result = await DeepResearch(topic).run(on_progress=on_progress)

        record = storage.get(research_id)
        if record is None:
            return
        record.status = Status.completed
        record.report = result.report
        record.report_html = result.report_html
        record.sources = result.sources
        record.draft = result.draft
        record.process = result.process
        record.confidence = result.confidence
        storage.save(record)
    except Exception as exc:
        storage.update_status(research_id, Status.failed, error=str(exc))
