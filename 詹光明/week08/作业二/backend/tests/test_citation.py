"""引用校验：编造的编号要被拦住，没来源的句子要被强制标注。

阶段 5 的验收：喂含 `[S99]` 的报告验证被拦截。
"""

from __future__ import annotations

from app.citation import (
    INFERRED_PREFIX,
    MISSING_CITATION_MARKER,
    check_text,
    find_markers,
    mark_inferred,
    merge_stats,
    normalize_sid,
    process_text,
    replace_unknown_markers,
    split_sentences,
)

VALID = {"S1", "S2", "S3"}


class TestNormalizeSid:
    def test_variants(self) -> None:
        for raw in ("3", 3, "S3", "s3", " S3 "):
            assert normalize_sid(raw) == "S3"

    def test_double_digit(self) -> None:
        assert normalize_sid("12") == "S12"


class TestFindMarkers:
    def test_in_order_with_duplicates(self) -> None:
        assert find_markers("甲[S2]乙[S1]丙[S2]") == ["S2", "S1", "S2"]

    def test_none(self) -> None:
        assert find_markers("没有任何引用") == []

    def test_empty(self) -> None:
        assert find_markers("") == []


class TestReplaceUnknownMarkers:
    def test_unknown_replaced_valid_kept(self) -> None:
        text, replaced = replace_unknown_markers("甲[S1]乙[S99]", VALID)

        assert replaced == 1
        assert text == f"甲[S1]乙{MISSING_CITATION_MARKER}"

    def test_all_valid_untouched(self) -> None:
        text, replaced = replace_unknown_markers("甲[S1]乙[S3]", VALID)

        assert replaced == 0
        assert text == "甲[S1]乙[S3]"

    def test_all_unknown(self) -> None:
        text, replaced = replace_unknown_markers("[S99][S100]", VALID)

        assert replaced == 2
        assert MISSING_CITATION_MARKER * 2 == text

    def test_no_markers(self) -> None:
        text, replaced = replace_unknown_markers("光秃秃的一句话", VALID)

        assert replaced == 0
        assert text == "光秃秃的一句话"


class TestSplitSentences:
    def test_chinese_terminators(self) -> None:
        assert split_sentences("第一句。第二句！第三句？") == ["第一句。", "第二句！", "第三句？"]

    def test_newline_is_boundary(self) -> None:
        assert split_sentences("第一句\n第二句") == ["第一句", "第二句"]

    def test_semicolon_splits(self) -> None:
        assert split_sentences("甲；乙") == ["甲；", "乙"]

    def test_empty(self) -> None:
        assert split_sentences("") == []


class TestMarkInferred:
    def test_uncited_sentence_gets_prefix(self) -> None:
        text, marked = mark_inferred("这是一句没有来源的话。", VALID)

        assert marked == 1
        assert text == f"{INFERRED_PREFIX}这是一句没有来源的话。"

    def test_cited_sentence_untouched(self) -> None:
        text, marked = mark_inferred("这句有来源[S1]。", VALID)

        assert marked == 0
        assert text == "这句有来源[S1]。"

    def test_idempotent(self) -> None:
        once, _ = mark_inferred("没有来源的话。", VALID)
        twice, marked = mark_inferred(once, VALID)

        assert twice == once
        assert marked == 0

    def test_llm_written_inferred_prefix_stripped_when_cited(self) -> None:
        """LLM 随手写「（模型推断）」但这句其实有引用 —— 程序按有无引用重判。"""
        text, _ = mark_inferred("（模型推断）这句其实有来源[S1]。", VALID)

        assert text == "这句其实有来源[S1]。"

    def test_llm_inferred_prefix_normalized_when_uncited(self) -> None:
        """LLM 自己标了「（模型推断）」但没引用 —— 换成程序统一的 ⚠️ 标记。"""
        text, marked = mark_inferred("（模型推断）确实没有来源。", VALID)

        assert marked == 1
        assert text == f"{INFERRED_PREFIX}确实没有来源。"

    def test_headings_skipped(self) -> None:
        text, marked = mark_inferred("## 二级标题\n正文没有来源。", VALID)

        assert marked == 1
        assert text.startswith("## 二级标题")
        assert INFERRED_PREFIX not in text.split("\n")[0]

    def test_table_rows_skipped(self) -> None:
        text, marked = mark_inferred("| 列甲 | 列乙 |\n正文没有来源。", VALID)

        assert marked == 1
        assert not text.startswith(INFERRED_PREFIX)

    def test_code_fence_skipped(self) -> None:
        source = "```python\nprint('没有引用')\n```\n正文没有来源。"
        text, marked = mark_inferred(source, VALID)

        assert marked == 1
        assert "print('没有引用')" in text

    def test_bullet_prefix_preserved(self) -> None:
        text, marked = mark_inferred("- 一条没有来源的要点。", VALID)

        assert marked == 1
        assert text == f"- {INFERRED_PREFIX}一条没有来源的要点。"

    def test_blank_lines_preserved(self) -> None:
        text, _ = mark_inferred("有来源[S1]。\n\n另一段有来源[S2]。", VALID)

        assert "\n\n" in text

    def test_empty_text(self) -> None:
        text, marked = mark_inferred("", VALID)

        assert text == ""
        assert marked == 0


class TestCheckText:
    def test_counts(self) -> None:
        stats = check_text("甲[S1]乙[S99]丙[S2]", VALID)

        assert stats.total_markers == 3
        assert stats.valid_markers == 2
        assert stats.unknown_markers == 1
        assert stats.unknown_ratio == 1 / 3

    def test_no_markers(self) -> None:
        stats = check_text("没有引用", VALID)

        assert stats.total_markers == 0
        assert stats.unknown_ratio == 0.0


class TestProcessText:
    def test_clean_text_fast_path(self) -> None:
        text, stats = process_text("有来源的句子[S1]。没有来源的句子。", VALID)

        assert stats.unknown_markers == 0
        assert stats.inferred_sentences == 1
        assert INFERRED_PREFIX in text
        assert "[S1]" in text

    def test_fabricated_sid_is_replaced(self) -> None:
        text, stats = process_text("编造的引用[S99]。", VALID)

        assert stats.unknown_markers == 1
        assert stats.replaced_markers == 1
        assert "[S99]" not in text
        assert MISSING_CITATION_MARKER in text
        # 替换掉编号后这句就没有依据了，必须被标成模型推断
        assert INFERRED_PREFIX in text

    def test_valid_marker_survives(self) -> None:
        text, _ = process_text("真的引用[S2]。", VALID)

        assert "[S2]" in text

    def test_citation_coverage(self) -> None:
        _, stats = process_text("有来源[S1]。没来源。也没来源。", VALID)

        assert stats.sentences == 3
        assert stats.inferred_sentences == 2
        assert abs(stats.citation_coverage - 1 / 3) < 1e-9

    def test_high_unknown_ratio_logged_not_crashed(self) -> None:
        """占比超标应该被记录并触发重写建议，但函数本身仍然正常工作。"""
        text, stats = process_text("[S98][S99][S100][S1]", VALID)

        assert stats.unknown_ratio == 0.75
        assert text.count(MISSING_CITATION_MARKER) == 3

    def test_empty_text(self) -> None:
        text, stats = process_text("", VALID)

        assert text == ""
        assert stats.sentences == 0
        assert stats.citation_coverage == 0.0


class TestMergeStats:
    def test_merges_counts(self) -> None:
        _, a = process_text("有来源[S1]。没来源。", VALID)
        _, b = process_text("编造的[S99]。", VALID)

        merged = merge_stats([a, b])

        assert merged.total_markers == 2
        assert merged.valid_markers == 1
        assert merged.unknown_markers == 1
        assert merged.sentences == 3
        assert merged.inferred_sentences == 2
        assert merged.unknown_ratio == 0.5

    def test_empty_list(self) -> None:
        merged = merge_stats([])

        assert merged.total_markers == 0
        assert merged.unknown_ratio == 0.0
