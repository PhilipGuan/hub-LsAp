import json
from pathlib import Path
import unittest
from unittest.mock import patch

import streamlit as st
from streamlit.delta_generator import DeltaGenerator
from streamlit.testing.v1 import AppTest


class FrontendStreamTests(unittest.TestCase):
    def test_answer_completes_before_table_is_displayed(self):
        result = {"columns": ["value"], "rows": [[1]], "selected_tables": ["test"]}
        events = [
            ("result", result),
            ("answer_delta", {"delta": "最终"}),
            ("answer_delta", {"delta": "答案"}),
            ("done", {**result, "answer": "最终答案", "status": "success", "steps": []}),
        ]
        phase = [None]
        rendered = []

        class Response:
            headers = {"Content-Type": "text/event-stream"}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def raise_for_status(self):
                pass

            def iter_lines(self, **kwargs):
                for event, data in events:
                    phase[0] = event
                    yield "event: " + event
                    yield "data: " + json.dumps(data)
                    yield ""

        original_table = st.dataframe
        original_markdown = DeltaGenerator.markdown

        def table(*args, **kwargs):
            self.assertEqual(phase[0], "done")
            rendered.append("table")
            return original_table(*args, **kwargs)

        def markdown(container, body, *args, **kwargs):
            if body == "最终答案":
                rendered.append("answer")
            return original_markdown(container, body, *args, **kwargs)

        page = AppTest.from_file(str(Path(__file__).parent / "frontend/app.py")).run(timeout=20)
        page.chat_input[0].set_value("测试查询")
        with patch("requests.post", return_value=Response()), \
                patch.object(st, "dataframe", side_effect=table), \
                patch.object(DeltaGenerator, "markdown", markdown):
            page.run(timeout=20)
        self.assertFalse(page.exception)
        self.assertLess(rendered.index("answer"), rendered.index("table"))
        self.assertEqual(len(page.dataframe), 1)


if __name__ == "__main__":
    unittest.main()
