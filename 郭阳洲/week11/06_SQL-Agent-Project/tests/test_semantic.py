from __future__ import annotations

import pytest

from sql_agent.semantic import MetricAmbiguousError, MetricUndefinedError


def test_load_metrics(semantic) -> None:
    assert semantic.data_from == "2009-01-01"
    assert semantic.data_until == "2013-12-22"
    ids = {metric.id for metric in semantic.metrics.values()}
    for metric_id in (
        "revenue",
        "revenue_line",
        "sales_volume",
        "customer_count",
        "customer_count_active",
        "track_count",
        "track_count_sold",
        "avg_spend_per_customer",
    ):
        assert metric_id in ids


@pytest.mark.parametrize(
    ("question", "tables", "expected"),
    [
        ("2010 年每个季度的销售额是多少？", ["invoices"], "revenue"),
        ("金属乐的销售额是多少？", ["invoices", "invoice_items", "tracks", "genres"], "revenue_line"),
        ("每个流派的销量是多少？", ["invoice_items", "tracks", "genres"], "sales_volume"),
        ("美国客户有多少位？", ["customers"], "customer_count"),
        ("摇滚流派有多少首歌曲？", ["tracks", "genres"], "track_count"),
        ("人均消费是多少？", ["invoices", "customers"], "avg_spend_per_customer"),
    ],
)
def test_bind_metric(semantic, question, tables, expected) -> None:
    binding = semantic.bind_metric(question, tables)
    assert binding is not None
    assert binding.metric.id == expected


def test_undefined_metric(semantic) -> None:
    with pytest.raises(MetricUndefinedError) as exc:
        semantic.bind_metric("客单价是多少？")
    assert exc.value.code == "E7005"
    assert exc.value.mention == "客单价"
    assert exc.value.alternatives


def test_ambiguous_metric(semantic) -> None:
    with pytest.raises(MetricAmbiguousError):
        semantic.bind_metric("销售额和销量分别是多少？")


@pytest.mark.parametrize(
    ("question", "start", "end", "granularity"),
    [
        ("2013年上半年每个月的销售额是多少？", "2013-01-01", "2013-06-30", "month"),
        ("2012年下半年销售额", "2012-07-01", "2012-12-31", None),
        ("2013 年 12 月的发票数量", "2013-12-01", "2013-12-22", None),
        ("最近一年的销售额是多少？", "2012-12-22", "2013-12-22", None),
        ("最近 3 年的销售额", "2011-01-01", "2013-12-22", None),
        ("上个月的销售额", "2013-11-01", "2013-11-30", "month"),
        ("2009 到 2011 年每年的销售额", "2009-01-01", "2011-12-31", None),
        ("2014年以后的发票数量是多少？", "2014-01-01", "2014-01-01", None),
    ],
)
def test_parse_time_range(semantic, question, start, end, granularity) -> None:
    time_range = semantic.parse_time_range(question)
    assert time_range.start == start
    assert time_range.end == end
    if granularity:
        assert time_range.granularity == granularity


def test_out_of_range_flag(semantic) -> None:
    assert semantic.parse_time_range("2015年的销售额是多少？").out_of_range
    assert semantic.parse_time_range("2013年的销售额是多少？").out_of_range is False


def test_unspecified_uses_full_range(semantic) -> None:
    time_range = semantic.parse_time_range("有多少张发票？")
    assert time_range.specified is False
    assert time_range.start == "2009-01-01"
    assert time_range.end == "2013-12-22"


def test_entity_aliases(semantic) -> None:
    aliases = semantic.resolve_aliases(
        "摇滚流派有多少首歌曲？", None, ["tracks", "genres"]
    )
    assert any(
        alias.table == "genres" and alias.column == "Name" and alias.value == "Rock"
        for alias in aliases
    )


def test_customer_country_alias(semantic) -> None:
    aliases = semantic.resolve_aliases(
        "美国客户的发票总数是多少张？", None, ["customers", "invoices"]
    )
    assert any(
        alias.table == "customers" and alias.column == "Country" and alias.value == "USA"
        for alias in aliases
    )


def test_resolve_produces_assumptions(semantic) -> None:
    resolution = semantic.resolve("2009 年每个月的销售额是多少？", ["invoices"])
    assert resolution.metric is not None
    assert resolution.metric.metric.id == "revenue"
    assert resolution.time_range.specified
    assert any("账单粒度" in item for item in resolution.assumptions)
