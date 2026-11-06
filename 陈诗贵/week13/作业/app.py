# -*- coding: utf-8 -*-
"""
Week13 作业2 · SQL Agent 对话式 Web 前端

后端只做一件事：把网页里输入的文本交给 LangGraph 的 SQL Agent，
再把 answer / sql / 节点轨迹回传前端渲染。

启动：
    E:\\anaconda3\\envs\\py312\\python.exe app.py
浏览器：
    http://127.0.0.1:5001
"""
import os
import sys
import time
import threading

from flask import Flask, jsonify, request, send_from_directory

# 让 app.py 能 import 同目录的 langgraph_sql_agent.py
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import langgraph_sql_agent as agent_mod

app = Flask(__name__, static_folder="static", static_url_path="")

# 图只编译一次，全进程复用（scan_db 也复用同一个 DBParser）
_graph = agent_mod.build_graph()
# Flask 默认多线程，SQLite 连接不是线程安全的，用锁把请求串行化
_lock = threading.Lock()


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/config")
def config():
    """前端初始化时读一下，用于页面上显示当前数据库和模型。"""
    return jsonify({
        "db": os.path.basename(agent_mod.DB_PATH),
        "db_path": agent_mod.DB_PATH,
        "model": agent_mod.LLM_MODEL,
        "has_key": bool(agent_mod.LLM_API_KEY),
    })


@app.route("/api/chat", methods=["POST"])
def chat():
    """接收 {"question": "..."}，跑一遍 LangGraph，返回结果。"""
    if not agent_mod.LLM_API_KEY:
        return jsonify({"error": "未配置 API Key，请检查项目根目录的 .env 中的 DS_API_KEY"}), 500

    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"error": "问题不能为空"}), 400

    start = time.time()
    try:
        with _lock:
            state = _graph.invoke({"question": question, "attempts": 0, "trace": []})
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500

    return jsonify({
        "answer": state.get("answer") or "",
        "sql": state.get("sql") or "",
        "trace": state.get("trace") or [],
        "elapsed": round(time.time() - start, 2),
    })


if __name__ == "__main__":
    print("=" * 60)
    print("SQL Agent 对话前端")
    print(f"  数据库 : {agent_mod.DB_PATH}")
    print(f"  模型   : {agent_mod.LLM_MODEL}")
    print(f"  Key    : {'已配置' if agent_mod.LLM_API_KEY else '未配置（请检查 root/.env）'}")
    print("  本机   : http://127.0.0.1:5001")
    print("=" * 60)
    app.run(host="0.0.0.0", port=5001, debug=False)
