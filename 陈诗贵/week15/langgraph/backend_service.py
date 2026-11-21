# -*- coding: utf-8 -*-
"""后端进程 · LangGraph 编排服务（端口 5000）

只做编排，不内嵌识别/生成逻辑：

    START → node_sentiment ──┬─(positive)→ node_encourage → END
                             └─(negative)→ node_comfort   → END

两个下游节点各自通过 HTTP 调用独立进程：
    进程1 :5002 /api/analyze   进程2 :5003 /api/reply

启动：
    python backend_service.py
调试：
    curl -X POST http://127.0.0.1:5000/api/chat -H "Content-Type: application/json" -d "{\"message\":\"今天升职了\"}"
"""
from typing import List, Optional, TypedDict

import requests
from flask import Flask, jsonify, request
from langgraph.graph import END, START, StateGraph

import common

SENTIMENT_URL = f"http://127.0.0.1:{common.SENTIMENT_PORT}/api/analyze"
REPLY_URL = f"http://127.0.0.1:{common.REPLY_PORT}/api/reply"


class ChatState(TypedDict, total=False):
    message: str
    sentiment: dict
    mode: str
    reply: str
    trace: List[str]
    error: Optional[str]


def _post_json(url: str, payload: dict, service_name: str) -> dict:
    """调下游服务；把各种失败统一转成中文错误。"""
    try:
        resp = requests.post(url, json=payload, timeout=common.RPC_TIMEOUT)
    except requests.RequestException as e:
        raise RuntimeError(f"{service_name}不可用: {type(e).__name__}") from e

    if resp.status_code != 200:
        try:
            detail = resp.json().get("error") or resp.text
        except ValueError:
            detail = resp.text
        raise RuntimeError(f"{service_name}返回 {resp.status_code}: {detail}")
    return resp.json()


def node_sentiment(state: ChatState) -> dict:
    """进程1：情感识别。失败时写 error，由条件边直接结束图。"""
    trace = state.get("trace", []) + ["node_sentiment"]
    try:
        sentiment = _post_json(SENTIMENT_URL, {"text": state["message"]}, "情感识别服务")
    except Exception as e:
        return {"trace": trace, "error": str(e)}
    return {"sentiment": sentiment, "trace": trace}


def _reply_node(state: ChatState, label: str, node_name: str) -> dict:
    """进程2：按 label 生成鼓励/排解回复。"""
    trace = state.get("trace", []) + [node_name]
    sentiment = state.get("sentiment") or {}
    try:
        data = _post_json(REPLY_URL, {
            "text": state["message"],
            "label": label,
            "reason": sentiment.get("reason", ""),
        }, "回复服务")
    except Exception as e:
        return {"trace": trace, "error": str(e)}
    return {"mode": data.get("mode"), "reply": data.get("reply", ""), "trace": trace}


def node_encourage(state: ChatState) -> dict:
    return _reply_node(state, "positive", "node_encourage")


def node_comfort(state: ChatState) -> dict:
    return _reply_node(state, "negative", "node_comfort")


def route_by_sentiment(state: ChatState) -> str:
    """条件边：先看错误，再看 label。"""
    if state.get("error"):
        return END
    label = (state.get("sentiment") or {}).get("label")
    return "node_encourage" if label == "positive" else "node_comfort"


def build_graph():
    graph = StateGraph(ChatState)
    graph.add_node("node_sentiment", node_sentiment)
    graph.add_node("node_encourage", node_encourage)
    graph.add_node("node_comfort", node_comfort)

    graph.add_edge(START, "node_sentiment")
    graph.add_conditional_edges(
        "node_sentiment",
        route_by_sentiment,
        {"node_encourage": "node_encourage", "node_comfort": "node_comfort", END: END},
    )
    graph.add_edge("node_encourage", END)
    graph.add_edge("node_comfort", END)
    return graph.compile()


_graph = build_graph()
app = Flask(__name__)


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "backend"})


@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "请求体必须是 JSON 对象"}), 400

    message = common.clip_text(data.get("message", ""))
    if not message:
        return jsonify({"error": "message 不能为空"}), 400

    state = _graph.invoke({"message": message, "trace": []})
    if state.get("error"):
        return jsonify({"error": state["error"], "trace": state.get("trace", [])}), 502

    return jsonify({
        "message": message,
        "sentiment": state.get("sentiment") or {},
        "mode": state.get("mode"),
        "reply": state.get("reply", ""),
        "trace": state.get("trace", []),
        "error": None,
    })


if __name__ == "__main__":
    print(f"[backend] :{common.BACKEND_PORT}  LangGraph 节点: {list(_graph.get_graph().nodes)}")
    app.run(host="127.0.0.1", port=common.BACKEND_PORT, debug=False, threaded=True)
