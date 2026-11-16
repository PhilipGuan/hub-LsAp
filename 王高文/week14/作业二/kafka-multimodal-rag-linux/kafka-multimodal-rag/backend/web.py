from uuid import uuid4

from flask import Flask, Response, jsonify, request

from .ingest import image_type
from .qa import ChatError

MAX_FILES = 10
MAX_IMAGE_BYTES = 5 * 1024 * 1024


def create_app(store, enqueue, chat, max_pdf_bytes=10 * 1024 * 1024):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_FILES * max_pdf_bytes + 1024 * 1024
    app.config["MAX_FORM_PARTS"] = 24

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/documents")
    def documents():
        return jsonify(store.documents())

    @app.errorhandler(413)
    def too_large(_):
        return {"error": "请求体超过上传限制。"}, 413

    @app.post("/api/documents")
    def upload():
        files = request.files.getlist("files")
        if not 1 <= len(files) <= MAX_FILES:
            return {"error": f"每次请选择 1 至 {MAX_FILES} 份 PDF。"}, 400
        accepted, errors = [], []
        send_failures = 0
        for file in files:
            name = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1][:240]
            data = file.stream.read(max_pdf_bytes + 1)
            if len(data) > max_pdf_bytes:
                errors.append({"name": name, "error": "超过单个 PDF 大小上限。"})
                continue
            if not name.lower().endswith(".pdf") or b"%PDF-" not in data[:1024]:
                errors.append({"name": name, "error": "请选择有效的 PDF 文件。"})
                continue
            doc_id = uuid4().hex
            try:
                enqueue(doc_id, name, data)
            except RuntimeError:
                app.logger.exception("Kafka delivery failed for %s", doc_id)
                errors.append({"name": name, "error": "Kafka 未确认写入，请稍后重试。"})
                send_failures += 1
                continue
            store.queued(doc_id, name)
            accepted.append(store.get(doc_id))
        status = 202 if accepted else (503 if send_failures else 400)
        return {"documents": accepted, "errors": errors}, status

    @app.get("/api/images/<image_id>")
    def image(image_id):
        result = store.image(image_id)
        if not result:
            return {"error": "图片不存在。"}, 404
        mime, data = result
        return Response(data, mimetype=mime, headers={"X-Content-Type-Options": "nosniff"})

    @app.post("/api/ask")
    def ask():
        question = request.form.get("question", "").strip()
        if not 1 <= len(question) <= 4000:
            return {"error": "请输入 1 至 4000 字的提问。"}, 400
        attachment = request.files.get("image")
        vision_image = None
        if attachment:
            data = attachment.stream.read(MAX_IMAGE_BYTES + 1)
            mime = image_type(data)
            if len(data) > MAX_IMAGE_BYTES or not mime:
                return {"error": "请选择不超过 5 MiB 的 PNG、JPEG 或 WebP。"}, 400
            vision_image = (mime, data)
        if chat is None:
            return {"error": "服务器尚未配置视觉模型。"}, 503
        try:
            return {"blocks": chat.ask(question, vision_image)}
        except ChatError as exc:
            return {"error": str(exc)}, 502

    return app
