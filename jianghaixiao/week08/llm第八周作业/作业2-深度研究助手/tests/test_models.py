"""数据模型测试。"""
from backend.models import ResearchRequest, ResearchRecord, Status


def test_research_request_requires_topic() -> None:
    request = ResearchRequest(topic="Agent 框架对比")
    assert request.topic == "Agent 框架对比"


def test_research_record_defaults() -> None:
    record = ResearchRecord(
        research_id="demo",
        topic="Agent 框架对比",
        status=Status.pending,
        created_at="2026-09-01T00:00:00+00:00",
        updated_at="2026-09-01T00:00:00+00:00",
    )
    assert record.report is None
    assert record.sources == []
    assert record.process.iterations == 0
