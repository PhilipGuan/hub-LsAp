"""正文提取的降级链与编码判定。

这两块都是「静默出错」的地方 —— 编解码猜错和来路标错都不会抛异常，
只会让报告悄悄变差，所以必须用测试钉死。
"""

from __future__ import annotations

import pytest

from app.extract import MIN_USEFUL_CHARS, REPLACEMENT_CHAR, clean_text, decode_html, extract_content
from app.models import ContentOrigin

# 一段足够长的中文正文，用来跨过 MIN_USEFUL_CHARS 门槛
_LONG_CN = "语文阅读理解能力的提升需要长期的阅读积累。" * 20


def _article(text: str, charset: str) -> bytes:
    return f'<html><head><meta charset="{charset}"></head><body><p>{text}</p></body></html>'.encode(
        "gb2312" if charset in ("gb2312", "gbk") else charset
    )


class TestDecodeHtml:
    def test_gb2312_declared_only_in_meta(self) -> None:
        """HTTP 头不带 charset、页面用 <meta> 声明 GB2312 —— 实测踩到的坑。

        httpx 此时会默认按 UTF-8 解，整页变乱码，所以必须自己看 meta。
        """
        raw = _article("中学生的语文阅读理解能力", "gb2312")
        # 模拟真实响应头：只有 text/html，没有 charset
        html = decode_html(raw, "text/html")

        assert REPLACEMENT_CHAR not in html
        assert "中学生的语文阅读理解能力" in html

    def test_utf8_page(self) -> None:
        raw = _article("中学生的语文阅读理解能力", "utf-8")
        html = decode_html(raw, "text/html; charset=utf-8")

        assert REPLACEMENT_CHAR not in html
        assert "中学生的语文阅读理解能力" in html

    def test_header_charset_beats_meta(self) -> None:
        """HTTP 头显式声明时优先信头 —— 有的站点 meta 是模板里写死的、跟正文编码不符。"""
        # 用繁体是因为 big5 编不出简体字，会直接抛 UnicodeEncodeError
        text = "繁體中文編碼測試"
        raw = text.encode("big5")
        html = decode_html(raw, "text/html; charset=big5")

        assert REPLACEMENT_CHAR not in html
        assert text in html

    def test_wrong_header_charset_still_recovers(self) -> None:
        """头里写错了编码（声称 utf-8 实际 GB2312）也要靠 meta 救回来。"""
        raw = _article("中学生的语文阅读理解能力", "gb2312")
        html = decode_html(raw, "text/html; charset=utf-8")

        assert REPLACEMENT_CHAR not in html
        assert "中学生的语文阅读理解能力" in html

    def test_no_declaration_falls_back(self) -> None:
        """完全没有编码声明时逐个试，最终应能认出 gb18030。"""
        raw = ("<html><body><p>中文字符编码测试</p></body></html>").encode("gb18030")
        html = decode_html(raw, "")

        assert "中文字符编码测试" in html

    def test_empty_bytes(self) -> None:
        assert decode_html(b"", "text/html") == ""


class TestExtractContent:
    def test_long_html_yields_page_origin(self) -> None:
        html = f"<html><body><article><p>{_LONG_CN}</p></article></body></html>"
        result = extract_content(html, "https://example.com/a")

        assert result.origin is ContentOrigin.page
        assert result.extractor in ("trafilatura", "readability", "bs4")
        assert len(result.text) >= MIN_USEFUL_CHARS

    def test_short_html_degrades_to_search_summary(self) -> None:
        """网页抽不出正文时退到搜索摘要，来路必须如实标成 search_summary。"""
        result = extract_content(
            "<html><body><p>短</p></body></html>",
            "https://example.com/a",
            search_summary=_LONG_CN,
        )

        assert result.origin is ContentOrigin.search_summary
        assert result.extractor == "bocha_summary"

    def test_short_summary_still_labelled_search_summary(self) -> None:
        """摘要短到进不了 MIN_USEFUL_CHARS 门槛，来路也还是 search_summary。

        标成 snippet 会连带把置信度算错 —— 这是同一个 bug 的另一副面孔。
        """
        result = extract_content("", "https://example.com/a", search_summary="很短的摘要")

        assert result.origin is ContentOrigin.search_summary
        assert result.extractor == "bocha_summary"

    def test_snippet_only_is_snippet_origin(self) -> None:
        result = extract_content("", "https://example.com/a", snippet="列表里的一行描述")

        assert result.origin is ContentOrigin.snippet
        assert result.extractor == "bocha_snippet"

    def test_summary_preferred_over_snippet(self) -> None:
        result = extract_content("", "https://example.com/a", search_summary="摘要", snippet="描述")

        assert result.text == "摘要"
        assert result.origin is ContentOrigin.search_summary

    def test_nothing_at_all(self) -> None:
        result = extract_content("", "https://example.com/a")

        assert result.text == ""
        assert result.ok is False
        assert result.extractor == "none"

    @pytest.mark.parametrize("html", ["<html></html>", "<html><body></body></html>", ""])
    def test_empty_documents_never_raise(self, html: str) -> None:
        result = extract_content(html, "https://example.com/a")

        assert result.text == ""
        assert result.origin is ContentOrigin.snippet


class TestCleanText:
    def test_collapses_blank_lines(self) -> None:
        assert clean_text("第一段\n\n\n\n第二段") == "第一段\n\n第二段"

    def test_normalizes_fullwidth_spaces(self) -> None:
        assert clean_text("中文　　中文") == "中文 中文"

    def test_strips_line_edges(self) -> None:
        assert clean_text("  前  \n  后  ") == "前\n后"

    def test_empty(self) -> None:
        assert clean_text("") == ""
