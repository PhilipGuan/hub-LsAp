import unittest

from agent.summary_agent import ThinkingStream, stream_summary
from models.workflow import QueryResult, SQLCandidate


class SummaryStreamTests(unittest.IsolatedAsyncioTestCase):
    async def collect(self, chunks):
        class FakeLLM:
            def make_agent(self, *args):
                return None

            async def stream_agent(self, *args):
                for chunk in chunks:
                    yield chunk

        result = QueryResult(columns=[], rows=[], returned_rows=0, truncated=False, elapsed_ms=0)
        events = [event async for event in stream_summary(
            FakeLLM(), "test", SQLCandidate(sql="SELECT 1"), result)]
        return "".join(events)

    async def test_tags_split_at_every_boundary(self):
        text = "<think>检查查询结果</think>最终答案"
        for index in range(len(text) + 1):
            with self.subTest(index=index):
                self.assertEqual(await self.collect([text[:index], text[index:]]),
                                 "最终答案")
        self.assertEqual(await self.collect(list(text)),
                         "最终答案")

    async def test_plain_answer_and_missing_tags(self):
        self.assertEqual(await self.collect(["直接", "回答"]),
                         "直接回答")
        self.assertEqual(await self.collect(["检查结果</think>答案"]),
                         "答案")
        self.assertEqual(await self.collect(["<think>未完成的思考"]),
                         "")

    def test_content_is_released_before_stream_finishes(self):
        parser = ThinkingStream()
        self.assertEqual(list(parser.feed("直接回答")), [("answer", "直接回答")])
        parser = ThinkingStream()
        self.assertEqual(list(parser.feed("<think>检查")), [("reasoning", "检查")])
        self.assertEqual(list(parser.feed("结果</thi")), [("reasoning", "结果")])
        self.assertEqual(list(parser.feed("nk>最终")), [("answer", "最终")])
        self.assertEqual(list(parser.feed("答案")), [("answer", "答案")])


if __name__ == "__main__":
    unittest.main()
