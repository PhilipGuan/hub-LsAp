import json
import tempfile
import unittest
from unittest.mock import patch
from app import create_record
from engine import run_research, normalize_claim, markdown
from providers import DemoProvider, LiveProvider, DEMO_TOPIC
from storage import Store


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def record(self):
        return create_record({"topic": DEMO_TOPIC, "mode": "demo", "max_rounds": 3})

    def test_demo_full_loop_persistence_and_export(self):
        r = self.record()
        run_research(r, self.store)
        saved = self.store.get(r["id"])
        self.assertEqual(saved["status"], "completed")
        self.assertEqual(saved["iterations"], 2)
        self.assertEqual(len(saved["sources"]), 2)
        self.assertEqual(len(saved["queries"]), 3)
        self.assertEqual(len([s for s in saved["process"] if s["kind"] == "judge"]), 2)
        self.assertIn("模型推断", markdown(saved))
        self.assertIn("未实时检索", markdown(saved))
        self.assertEqual(saved["confidence"]["citation_coverage"], "2/3")

    def test_bad_citations_are_inference(self):
        for ids in ([999], [1, 999], [True], ["1"], "1"):
            self.assertEqual(normalize_claim({"text": "x", "source_ids": ids}, {1})["source_ids"], [])
        self.assertEqual(normalize_claim({"text": "x", "source_ids": [1, 1]}, {1})["source_ids"], [1])

    def test_empty_topic_invalid_mode_rounds(self):
        for payload in ({"topic": " "}, {"topic": DEMO_TOPIC, "mode": "bad"},
                        {"topic": DEMO_TOPIC, "max_rounds": True},
                        {"topic": DEMO_TOPIC, "max_rounds": 9}, {"topic": "其他自定义主题"}):
            with self.assertRaises(ValueError):
                create_record(payload)

    def test_no_sources_failure_preserves_process(self):
        class Empty(DemoProvider):
            def search(self, query, round_number):
                return []
        r = self.record()
        run_research(r, self.store, Empty())
        saved = self.store.get(r["id"])
        self.assertEqual(saved["status"], "failed")
        self.assertIn("没有获得", saved["error"])
        self.assertGreater(len(saved["process"]), 3)
        self.assertIsNone(saved["report"])

    def test_max_rounds_is_enforced(self):
        class NeverEnough(DemoProvider):
            def judge(self, topic, blocks, sources, searched, round_number):
                return {"sufficient": False, "reason": "需要补检", "queries": ["new query " + str(round_number)]}
        r = self.record()
        run_research(r, self.store, NeverEnough())
        self.assertEqual(r["iterations"], 3)
        self.assertIn("上限", r["stop_reason"])

    def test_missing_config_fails_explicitly(self):
        with patch.dict('os.environ', {"LLM_API_KEY": "", "BOCHA_API_KEY": ""}):
            with self.assertRaisesRegex(ValueError, "LLM_API_KEY"):
                LiveProvider()

    def test_recovery_and_path_guard(self):
        r = self.record()
        self.store.save(r)
        self.store.recover()
        self.assertEqual(self.store.get(r["id"])["status"], "interrupted")
        with self.assertRaises(ValueError):
            self.store.get("../../.env")

    def test_provider_failure_retains_draft(self):
        class Broken(DemoProvider):
            def report(self, *args):
                raise RuntimeError("模型返回了无效 JSON")
        r = self.record()
        run_research(r, self.store, Broken())
        self.assertEqual(r["status"], "failed")
        self.assertTrue(r["draft"])
        self.assertTrue(r["sources"])


if __name__ == '__main__':
    unittest.main()
