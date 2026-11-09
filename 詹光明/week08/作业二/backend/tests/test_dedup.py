"""URL 归一化与内容去重的测试。"""

from __future__ import annotations

import pytest

from app.dedup import (
    CONTENT_JACCARD_THRESHOLD,
    TITLE_JACCARD_THRESHOLD,
    content_hash,
    content_similarity,
    normalize_url,
    path_key,
    registrable_domain,
    title_similarity,
)


class TestNormalizeUrl:
    @pytest.mark.parametrize(
        "raw",
        [
            "https://example.com/a/b.html",
            "https://example.com/a/b.html#section",
            "https://example.com/a/b.html?utm_source=wechat&utm_medium=share",
            "http://example.com/a/b.html",
            "https://EXAMPLE.com/a/b.html",
            "https://example.com/a//b.html",
        ],
    )
    def test_equivalent_forms_collapse(self, raw: str) -> None:
        assert normalize_url(raw) == "https://example.com/a/b.html"

    def test_trailing_slash_removed_but_root_kept(self) -> None:
        assert normalize_url("https://example.com/a/") == "https://example.com/a"
        assert normalize_url("https://example.com/") == "https://example.com/"

    @pytest.mark.parametrize("index_file", ["index.html", "index.php", "default.html"])
    def test_index_files_stripped(self, index_file: str) -> None:
        assert normalize_url(f"https://example.com/news/{index_file}") == (
            "https://example.com/news"
        )

    def test_non_tracking_params_are_kept_and_sorted(self) -> None:
        result = normalize_url("https://example.com/a?b=2&a=1&utm_source=x")
        assert result == "https://example.com/a?a=1&b=2"

    def test_default_port_removed_non_default_kept(self) -> None:
        assert normalize_url("https://example.com:443/a") == "https://example.com/a"
        assert normalize_url("https://example.com:8443/a") == "https://example.com:8443/a"

    def test_non_http_scheme_passthrough(self) -> None:
        assert normalize_url("mailto:a@b.com") == "mailto:a@b.com"

    def test_empty_input(self) -> None:
        assert normalize_url("") == ""


class TestPathKey:
    def test_extension_ignored(self) -> None:
        """同一个页面的 .html 与非 .html 形式应收敛成同一个键。"""
        assert path_key("https://example.com/a/123.html") == path_key("https://example.com/a/123")

    def test_different_articles_stay_distinct(self) -> None:
        assert path_key("https://example.com/a/123") != path_key("https://example.com/a/124")


class TestRegistrableDomain:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://www.36kr.com/p/1", "36kr.com"),
            ("https://36kr.com/p/1", "36kr.com"),
            ("https://mp.weixin.qq.com/s/abc", "qq.com"),
            ("https://www.gov.cn/zhengce/1", "gov.cn"),
        ],
    )
    def test_known_cases(self, url: str, expected: str) -> None:
        assert registrable_domain(url) == expected

    def test_empty(self) -> None:
        assert registrable_domain("") == ""


class TestContentSimilarity:
    # 真实正文长度（抓取后会截到 6000 字以内），这里取一个偏保守的长度
    _BASE = (
        "新能源汽车出口政策在2024年发生了重要变化，主要包括关税调整与配额管理等方面。"
        "行业分析师认为这一轮调整将重塑全球供应链格局，并对整车厂的出海节奏产生直接影响。"
    ) * 5

    def test_near_duplicates_detected(self) -> None:
        """改几个字的转载文章必须被判为重复。"""
        variant = self._BASE.replace("重要变化", "重要变革").replace("配额管理", "配额管控")
        assert content_similarity(self._BASE, variant) >= CONTENT_JACCARD_THRESHOLD

    def test_different_topics_not_duplicates(self) -> None:
        other = "如何用 Rust 实现一个无锁的并发队列，涉及原子操作与内存序的深入讨论。" * 5
        assert content_similarity(self._BASE, other) < CONTENT_JACCARD_THRESHOLD

    def test_same_topic_different_articles_not_duplicates(self) -> None:
        """最危险的误判：两篇讲同一件事但各写各的报道，绝不能被当成重复。

        这是决定阈值取值的实测依据 —— 同主题不同措辞实测仅 0.06 左右，
        离 0.75 有一个数量级的余量。
        """
        a = (
            "2024年中国新能源汽车出口量达到128万辆，同比增长6.7%。比亚迪、特斯拉与上汽"
            "位列出口前三，其中比亚迪出口43.3万辆。欧盟加征关税后，部分车企转向东南亚布局。"
        )
        b = (
            "据海关总署数据，2024年我国新能源汽车出口128.4万辆，较上年增长6.7个百分点。"
            "从企业看，比亚迪以43.3万辆居首，特斯拉中国与上汽集团紧随其后，多家厂商加快海外建厂。"
        )
        assert content_similarity(a, b) < CONTENT_JACCARD_THRESHOLD

    def test_syndicated_copy_is_duplicate(self) -> None:
        """真转载（改几处措辞 + 加个转载引子）必须被判为重复。"""
        original = (
            "2024年中国新能源汽车出口量达到128万辆，同比增长6.7%。比亚迪、特斯拉与上汽"
            "位列出口前三，其中比亚迪出口43.3万辆。欧盟加征关税后，部分车企转向东南亚布局。"
        )
        copy = "【转载】" + original.replace("转向东南亚布局", "转向东南亚及拉美布局")
        assert content_similarity(original, copy) >= CONTENT_JACCARD_THRESHOLD

    def test_identical_text(self) -> None:
        assert content_similarity(self._BASE, self._BASE) == 1.0

    def test_empty_input(self) -> None:
        assert content_similarity("", "x") == 0.0


class TestContentHash:
    def test_whitespace_and_punctuation_ignored(self) -> None:
        assert content_hash("你好，世界！") == content_hash("你好 世界")

    def test_different_content_differs(self) -> None:
        assert content_hash("内容甲") != content_hash("内容乙")


class TestTitleSimilarity:
    def test_near_identical_titles(self) -> None:
        a = "2025年国内AI编程助手市场主要玩家与定价对比"
        b = "2025年国内AI编程助手市场主要玩家与定价对比分析"
        assert title_similarity(a, b) >= TITLE_JACCARD_THRESHOLD

    def test_unrelated_titles(self) -> None:
        assert title_similarity("新能源汽车出口政策", "Rust 异步运行时调度器") < 0.3

    def test_empty_input(self) -> None:
        assert title_similarity("", "x") == 0.0
