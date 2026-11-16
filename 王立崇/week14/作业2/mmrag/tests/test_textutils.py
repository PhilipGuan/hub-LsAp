import unittest

from app.textutils import (
    html_table_to_text,
    is_visual_query,
    search_expression,
    split_text,
    tokenize,
)


class TextUtilsTest(unittest.TestCase):
    def test_html_table_keeps_first_row(self):
        html = (
            "<table><tr><th>场景</th><th>能力</th></tr>"
            "<tr><td>FAQ</td><td>匹配</td></tr></table>"
        )
        self.assertEqual(
            html_table_to_text(html),
            "| 场景 | 能力 |\n| FAQ | 匹配 |",
        )

    def test_tokenize_and_expression_are_fts_safe(self):
        tokens = tokenize("五种 QA 问答场景有什么区别？")
        expression = search_expression(tokens)
        self.assertIn("五种", expression)
        self.assertIn('"', expression)
        self.assertNotIn(":", expression)

    def test_split_text_respects_limit(self):
        text = "。".join(["这是一个测试句子" * 8] * 20)
        pieces = split_text(text, max_chars=200)
        self.assertTrue(pieces)
        self.assertTrue(all(len(piece) <= 200 for piece in pieces))

    def test_visual_query_detection(self):
        self.assertTrue(is_visual_query("请解释这张图中的流程"))
        self.assertTrue(is_visual_query("the diagram above"))
        self.assertFalse(is_visual_query("文档讲了哪些主题"))


if __name__ == "__main__":
    unittest.main()
