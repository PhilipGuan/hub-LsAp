"""S2 单测：来源登记表的 sid 分配、URL 去重、防幻觉引用。"""
import pytest

from research.models import Conclusion, Finding, SearchHit, Section, SourceRegistry


def test_register_assigns_sequential_sids():
    reg = SourceRegistry()
    s1, created1 = reg.register(SearchHit(url="https://a.com/1", title="A"))
    s2, created2 = reg.register(SearchHit(url="https://b.com/", title="B"))
    assert created1 and created2
    assert (s1.sid, s2.sid) == ("S1", "S2")


def test_register_dedup_ignores_tracking_params_and_case():
    reg = SourceRegistry()
    s1, _ = reg.register(SearchHit(url="https://a.com/x?utm_source=weixin&id=1", title="A"))
    s2, created = reg.register(SearchHit(url="https://A.com/x?id=1#top", title="A2"))
    assert created is False
    assert s2.sid == s1.sid == "S1"


def test_register_rejects_invalid_url():
    reg = SourceRegistry()
    with pytest.raises(ValueError):
        reg.register(SearchHit(url="javascript:alert(1)", title="x"))


def test_add_finding_filters_unknown_sids():
    reg = SourceRegistry()
    reg.register(SearchHit(url="https://a.com/1", title="A"))

    ok = reg.add_finding(Finding(content="材料", source_ids=["S1", "S99"]))
    assert ok.source_ids == ["S1"]
    assert reg.add_finding(Finding(content="材料", source_ids=["S99"])) is None


def test_finding_without_source_is_inference():
    reg = SourceRegistry()
    finding = reg.add_finding(Finding(content="材料", source_ids=[]))
    assert finding.is_inference


def test_findings_by_sub():
    reg = SourceRegistry()
    reg.add_finding(Finding(content="a", related_sub_id="Q1"))
    reg.add_finding(Finding(content="b", related_sub_id="Q2"))
    assert [f.content for f in reg.findings_by_sub("Q1")] == ["a"]


def test_refs_string_tolerated():
    """LLM 偶发输出 "S1, S2" 字符串，应自动拆为列表（真实冒烟中踩过的坑）。"""
    section = Section(heading="H", body="B", refs="S1, S2")
    assert section.refs == ["S1", "S2"]
    conclusion = Conclusion(text="C", refs="S3")
    assert conclusion.refs == ["S3"]


def test_latest_date():
    reg = SourceRegistry()
    reg.register(SearchHit(url="https://a.com/1", title="A", date_published="2026-08-01T00:00:00Z"))
    reg.register(SearchHit(url="https://b.com/1", title="B", date_published="2026-08-15"))
    reg.register(SearchHit(url="https://c.com/1", title="C"))  # 无时间
    assert reg.latest_date() == "2026-08-15"
