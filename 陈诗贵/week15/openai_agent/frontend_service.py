# -*- coding: utf-8 -*-
"""前端进程 · 聊天页面服务（端口 5011）

托管 static/ 页面，并把流式请求原样转发给后端（:5010）。
SSE 用流式转发（iter_content），不缓存整段内容，保证逐字到达浏览器。

启动：
    python frontend_service.py
访问：
    http://127.0.0.1:5011
"""
import requests
from flask import Flask, Response, jsonify, request, send_from_directory

import common

BACKEND_BASE = f"http://127.0.0.1:{common.BACKEND_PORT}"

app = Flask(__name__, static_folder="static", static_url_path="")


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "frontend"})


@app.route("/api/chat", methods=["POST"])
def chat():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "请求体必须是 JSON 对象"}), 400

    try:
        resp = requests.post(f"{BACKEND_BASE}/api/chat", json=payload, timeout=common.RPC_TIMEOUT)
    except requests.RequestException as e:
        return jsonify({"error": f"后端服务不可用: {type(e).__name__}"}), 502

    try:
        body = resp.json()
    except ValueError:
        return jsonify({"error": "后端返回了非 JSON 内容"}), 502
    return jsonify(body), resp.status_code


@app.route("/api/chat/stream", methods=["POST"])
def chat_stream():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "请求体必须是 JSON 对象"}), 400

    try:
        upstream = requests.post(f"{BACKEND_BASE}/api/chat/stream", json=payload,
                                 stream=True, timeout=common.RPC_TIMEOUT)
    except requests.RequestException as e:
        return jsonify({"error": f"后端服务不可用: {type(e).__name__}"}), 502

    # 后端的 400/502 是普通 JSON，直接透传状态码
    if upstream.status_code != 200:
        try:
            body = upstream.json()
        except ValueError:
            body = {"error": f"后端返回 {upstream.status_code}"}
        upstream.close()
        return jsonify(body), upstream.status_code

    def relay():
        try:
            for chunk in upstream.iter_content(chunk_size=None):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    return Response(relay(), mimetype="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


if __name__ == "__main__":
    print(f"[frontend] :{common.FRONTEND_PORT}  → 后端 :{common.BACKEND_PORT}")
    app.run(host=common.BIND_HOST, port=common.FRONTEND_PORT, debug=False, threaded=True)
