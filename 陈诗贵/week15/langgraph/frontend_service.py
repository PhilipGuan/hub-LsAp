# -*- coding: utf-8 -*-
"""前端进程 · 聊天页面服务（端口 5001）

职责：托管 static/ 页面，并把页面的 /api/chat 请求转发给后端进程（:5000）。
前端不直接跨端口请求后端，避免 CORS。

启动：
    python frontend_service.py
访问：
    http://127.0.0.1:5001
"""
import requests
from flask import Flask, jsonify, request, send_from_directory

import common

BACKEND_URL = f"http://127.0.0.1:{common.BACKEND_PORT}/api/chat"

app = Flask(__name__, static_folder="static", static_url_path="")


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "frontend"})


@app.route("/api/chat", methods=["POST"])
def chat():
    """原样转发到后端，状态码与错误信息一起透传，页面据此提示。"""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "请求体必须是 JSON 对象"}), 400

    try:
        resp = requests.post(BACKEND_URL, json=payload, timeout=common.RPC_TIMEOUT)
    except requests.RequestException as e:
        return jsonify({"error": f"后端服务不可用: {type(e).__name__}"}), 502

    try:
        body = resp.json()
    except ValueError:
        return jsonify({"error": "后端返回了非 JSON 内容"}), 502
    return jsonify(body), resp.status_code


if __name__ == "__main__":
    print(f"[frontend] :{common.FRONTEND_PORT}  → 后端 :{common.BACKEND_PORT}")
    app.run(host="127.0.0.1", port=common.FRONTEND_PORT, debug=False, threaded=True)
