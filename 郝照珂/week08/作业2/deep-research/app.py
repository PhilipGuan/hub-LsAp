"""Local-first HTTP application; no third-party dependencies required."""
import argparse
import json
import mimetypes
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from engine import markdown, now, run_research
from providers import DEMO_TOPIC, load_env
from storage import Store

ROOT = Path(__file__).resolve().parent
load_env(ROOT / ".env")
store = Store(ROOT / "data")
pool = ThreadPoolExecutor(max_workers=2)
slots = threading.BoundedSemaphore(4)


def create_record(payload):
    topic = payload.get("topic", "")
    mode = payload.get("mode", "demo")
    rounds = payload.get("max_rounds", 3)
    if not isinstance(topic, str) or not 2 <= len(topic.strip()) <= 200:
        raise ValueError("研究主题需要 2 至 200 个字符。")
    if mode not in ("demo", "live"):
        raise ValueError("无效研究模式。")
    if type(rounds) is not int or not 2 <= rounds <= 4:
        raise ValueError("轮数需为 2 至 4 的整数。")
    if mode == "demo" and topic.strip() != DEMO_TOPIC:
        raise ValueError("演示模式使用固定主题“RAG 与微调如何选择”；自定义主题请选择实时模式。")
    return {"id": uuid.uuid4().hex, "topic": topic.strip(), "mode": mode, "max_rounds": rounds,
            "status": "pending", "created_at": now(), "updated_at": now(), "sources": [], "process": [],
            "questions": [], "queries": [], "draft": [], "iterations": 0, "report": None, "confidence": None}


def worker(record):
    try:
        run_research(record, store)
    finally:
        slots.release()


class Handler(BaseHTTPRequestHandler):
    def send(self, status, body, content_type="application/json; charset=utf-8", filename=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        if filename:
            self.send_header("Content-Disposition", 'attachment; filename="' + filename + '"')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        try:
            if path == "/health":
                return self.send(200, {"status": "ok", "application": "week08-deep-research"})
            if path == "/api/research":
                return self.send(200, store.list())
            if path.startswith("/api/research/"):
                parts = path.strip("/").split("/")
                record = store.get(parts[2])
                if len(parts) == 3:
                    return self.send(200, record)
                if len(parts) == 4 and parts[3] == "json":
                    return self.send(200, record, filename=record["id"] + ".json")
                if len(parts) == 4 and parts[3] == "markdown":
                    if record["status"] != "completed":
                        return self.send(409, {"error": "报告尚未完成。"})
                    return self.send(200, markdown(record), "text/markdown; charset=utf-8", "research.md")
                return self.send(404, {"error": "接口不存在"})
            files = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css", "/favicon.svg": "favicon.svg"}
            if path not in files:
                return self.send(404, {"error": "页面不存在"})
            filename = files[path]
            return self.send(200, (ROOT / "dist" / filename).read_bytes(), (mimetypes.guess_type(filename)[0] or "text/plain") + "; charset=utf-8")
        except (FileNotFoundError, ValueError):
            self.send(404, {"error": "任务不存在或编号无效。"})

    def do_POST(self):
        if self.path != "/api/research":
            return self.send(404, {"error": "接口不存在"})
        # Restrict cross-origin writes to this local app.
        origin = self.headers.get("Origin")
        if origin and origin != "http://" + self.headers.get("Host", ""):
            return self.send(403, {"error": "不允许跨来源创建任务。"})
        if self.headers.get_content_type() != "application/json":
            return self.send(415, {"error": "需要 application/json"})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 10000:
                raise ValueError("请求大小无效")
            payload = json.loads(self.rfile.read(size))
            if not isinstance(payload, dict):
                raise ValueError("请求必须是 JSON 对象")
            record = create_record(payload)
        except (ValueError, UnicodeDecodeError) as exc:
            return self.send(400, {"error": str(exc)})
        if not slots.acquire(blocking=False):
            return self.send(429, {"error": "研究队列已满，请稍后重试。"})
        try:
            store.save(record)
            pool.submit(worker, record)
        except Exception:
            slots.release()
            return self.send(500, {"error": "无法保存或创建任务。"})
        self.send(202, {"research_id": record["id"]})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8008)
    args = parser.parse_args()
    store.recover()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Deep Research: http://127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        pool.shutdown(wait=True)
