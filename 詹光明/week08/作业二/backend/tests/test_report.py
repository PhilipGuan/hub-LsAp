"""报告渲染里「来源到底贡献了什么」这一列。

来源表是可追溯性的落点，但它的「引用」列早先只有两种取值：``✅`` 和
``— 已读未引``。于是**三种完全不同的情况挤在同一格里**：

1. 读了、抽出了笔记、被引用了
2. 读了、抽出了笔记、没被引用 —— 这是**选择**，本身是有价值的信息
3. 读了、一条笔记都没抽出来 —— 这是**失败**，该来源对结论毫无贡献

第 3 种在实测里真的会发生（推理模型的思考吃满 max_tokens，content 为空，
topic C 上 5/30 条来源如此），却被渲染成第 2 种，读起来像「读过但没用上」。
把失败说成选择，等于伪造了来源的可信度，所以这几种必须分开。
"""

from __future__ import annotations

from app.models import ContentOrigin, FetchStatus, Report, SourceRef
from app.report import render_markdown


def _source(sid: str, **kwargs: object) -> SourceRef:
    base: dict[str, object] = {
        "sid": sid,
        "title": f"标题 {sid}",
        "url": f"https://example.com/{sid}",
        "fetch_status": FetchStatus.ok,
        "content_origin": ContentOrigin.page,
        "readable": True,
    }
    base.update(kwargs)
    return SourceRef(**base)  # type: ignore[arg-type]


def _table_row(markdown: str, sid: str) -> str:
    return next(line for line in markdown.splitlines() if line.startswith(f"| [{sid}]"))


class TestSourceTableHonesty:
    def test_cited_source_marked_cited(self) -> None:
        report = Report(topic="t", sources=[_source("S1", cited=True, note_count=3)])

        assert "✅" in _table_row(render_markdown(report), "S1")

    def test_read_but_unused_is_a_choice(self) -> None:
        """读了、抽出了笔记、没被引用 —— 标注「已读未引」，别和失败混为一谈。"""
        report = Report(topic="t", sources=[_source("S1", note_count=4)])

        row = _table_row(render_markdown(report), "S1")
        assert "已读未引" in row
        assert "⚠️" not in row

    def test_read_failure_is_not_dressed_up_as_a_choice(self) -> None:
        """抽 0 条笔记 == 这条来源对结论没有任何贡献，必须显式标出来。"""
        report = Report(topic="t", sources=[_source("S1", note_count=0)])

        row = _table_row(render_markdown(report), "S1")
        assert "⚠️" in row
        assert "已读未引" not in row

    def test_never_read_source_is_not_called_a_failure(self) -> None:
        """重复来源 / 只剩片段压根没送去读 —— 标成「阅读失败」同样是失真。"""
        report = Report(
            topic="t",
            sources=[
                _source("S1", readable=False, content_origin=ContentOrigin.snippet),
            ],
        )

        row = _table_row(render_markdown(report), "S1")
        assert "⚠️" not in row
        assert "未参与阅读" in row

    def test_all_sources_still_listed(self) -> None:
        """降级 / 判重 / 读失败 —— 一条都不许从来源表里消失。"""
        report = Report(
            topic="t",
            sources=[
                _source("S1", cited=True, note_count=2),
                _source("S2", note_count=0),
                _source("S3", readable=False, content_origin=ContentOrigin.snippet),
                _source(
                    "S4",
                    fetch_status=FetchStatus.blocked,
                    content_origin=ContentOrigin.search_summary,
                    note_count=1,
                ),
            ],
        )

        markdown = render_markdown(report)
        for sid in ("S1", "S2", "S3", "S4"):
            assert f"[{sid}]" in markdown
        assert markdown.count("| [S") == 4
