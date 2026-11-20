import base64
import io
import json
import tempfile
import unittest
from pathlib import Path

import httpx

from backend.ingest import MinerU, process_job
from backend.qa import VisionChat
from backend.store import Store
from backend.web import create_app


PDF = b"%PDF-1.7\nraw\x00\xff\n%%EOF"
IMAGE = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a6yQAAAAASUVORK5CYII="
)
IMAGE_URL = "data:image/png;base64," + base64.b64encode(IMAGE).decode()


def mineru_response():
    return {"results": {"document": {"content_list": json.dumps([
        {"type": "text", "text": "滤波器实验在 532 nm 测得透射峰。", "page_idx": 2},
        {"type": "image", "img_path": "images/spectrum.png", "image_caption": ["图 1 光谱透射曲线"], "page_idx": 2},
        {"type": "text", "text": "封面介绍和出版声明。", "page_idx": 0},
    ]), "images": {"spectrum.png": IMAGE_URL}}}}


class AppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "rag.db")
        self.jobs = []
        self.app = create_app(self.store, lambda *args: self.jobs.append(args), None)
        self.client = self.app.test_client()

    def upload(self):
        return self.client.post("/api/documents", data={"files": [
            (io.BytesIO(PDF), "实验报告.pdf"), (io.BytesIO(PDF + b"2"), "附录.pdf"),
            (io.BytesIO(b"bad"), "坏文件.pdf"),
        ]})

    def parse(self, job):
        def handler(request):
            doc_id = job[0]
            self.assertEqual(self.store.get(doc_id)["status"], "parsing")
            self.assertIn(job[2], request.read())
            return httpx.Response(200, json=mineru_response())

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            process_job(self.store, MinerU("http://mineru/file_parse", client=client).parse, *job)

    def test_batch_enqueues_original_bytes_and_reports_invalid_file(self):
        result = self.upload()
        self.assertEqual(result.status_code, 202)
        self.assertEqual([job[2] for job in self.jobs], [PDF, PDF + b"2"])
        self.assertEqual(len(result.json["errors"]), 1)
        self.assertEqual([row["status"] for row in self.client.get("/api/documents").json], ["queued", "queued"])

    def test_parse_publishes_index_and_source_image_once(self):
        self.upload()
        job = self.jobs[0]
        self.parse(job)
        process_job(self.store, lambda _: self.fail("completed job was parsed again"), *job)
        self.store.queued(*job[:2])  # A fast consumer can finish before the upload returns.
        self.assertEqual(self.store.get(job[0])["status"], "complete")
        matches = self.store.search("光谱透射曲线")
        figures = [hit for hit in matches if hit["image_id"]]
        self.assertEqual(len(figures), 1)
        self.assertEqual(figures[0]["page"], 3)
        image = self.client.get("/api/images/" + figures[0]["image_id"])
        self.assertEqual(image.data, IMAGE)
        self.assertEqual(image.mimetype, "image/png")

    def test_one_mineru_failure_does_not_block_the_next_document(self):
        self.upload()
        with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(500))) as client:
            process_job(self.store, MinerU("http://mineru/file_parse", client=client).parse, *self.jobs[0])
        self.parse(self.jobs[1])
        self.assertEqual(self.store.get(self.jobs[0][0])["status"], "failed")
        self.assertEqual(self.store.get(self.jobs[1][0])["status"], "complete")

    def test_vision_answer_includes_original_query_image_and_known_figure(self):
        self.upload()
        self.parse(self.jobs[0])
        inputs = []

        def handler(request):
            parts = json.loads(request.content)["messages"][-1]["content"]
            inputs.append(parts)
            if len(inputs) == 1:
                result = "光谱透射曲线 532 nm"
            else:
                evidence = json.loads(parts[0]["text"])["evidence"]
                figure = next(item["id"] for item in evidence if item["has_image"])
                result = json.dumps({"blocks": [{"text": "透射峰在 532 nm。", "sources": [figure, 99], "figure": figure}]})
            return httpx.Response(200, json={"choices": [{"message": {"content": result}}]})

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            chat = VisionChat(self.store, "http://model/v1", "vision-model", "", client=client)
            app = create_app(self.store, None, chat).test_client()
            response = app.post("/api/ask", data={"question": "这张图的峰在哪里？", "image": (io.BytesIO(IMAGE), "plot.png")})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(inputs[0][1]["image_url"]["url"], IMAGE_URL)
        self.assertEqual(inputs[1][1]["image_url"]["url"], IMAGE_URL)
        block = response.json["blocks"][0]
        self.assertEqual(block["sources"], [{"document": "实验报告.pdf", "page": 3}])
        self.assertTrue(block["figure"]["url"].startswith("/api/images/"))


if __name__ == "__main__":
    unittest.main()
