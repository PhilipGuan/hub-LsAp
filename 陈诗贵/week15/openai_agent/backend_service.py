# -*- coding: utf-8 -*-
"""后端进程 · OpenAI Agents SDK 编排服务（端口 5010）

TriageAgent 负责分流：
    1. 先调用 analyze_sentiment 工具（HTTP → 进程1 :5012）
    2. 根据 label 用 SDK 原生 handoff 交给 Encourage / Comfort Agent
    3. 接手的 Agent 调用 generate_* 工具（HTTP → 进程2 :5013）生成回复

对外提供：
    POST /api/chat         非流式，返回完整结果
    POST /api/chat/stream  SSE 流式（agent / tool / handoff / delta / done / error）

启动：
    python backend_service.py
"""
import asyncio
import json
import queue
import threading
import time
from typing import Callable, Dict, List, Optional

import requests
from flask import Flask, Response, jsonify, request

import common

common.init_sdk()

from agents import Agent, Runner, function_tool  # noqa: E402

SENTIMENT_URL = f"http://127.0.0.1:{common.SENTIMENT_PORT}/api/analyze"
REPLY_URL = f"http://127.0.0.1:{common.REPLY_PORT}/api/reply"


# ----------------------------------------------------------------------
# 工具：模型通过这些工具跨进程调用子服务
# ----------------------------------------------------------------------
@function_tool
def analyze_sentiment(text: str) -> str:
    """调用情感识别进程，返回 JSON 字符串：{"label","score","reason"}。做任何判断前必须先调用本工具。"""
    try:
        resp = requests.post(SENTIMENT_URL, json={"text": text}, timeout=common.RPC_TIMEOUT)
    except requests.RequestException as e:
        raise RuntimeError(f"情感识别服务不可用: {type(e).__name__}") from e
    if resp.status_code != 200:
        try:
            detail = resp.json().get("error") or resp.text
        except ValueError:
            detail = resp.text
        raise RuntimeError(f"情感识别服务返回 {resp.status_code}: {detail}")
    return resp.text


def _call_reply(text: str, reason: str, label: str) -> str:
    try:
        resp = requests.post(REPLY_URL, json={
            "text": text, "label": label, "reason": reason,
        }, timeout=common.RPC_TIMEOUT)
    except requests.RequestException as e:
        raise RuntimeError(f"回复服务不可用: {type(e).__name__}") from e
    if resp.status_code != 200:
        try:
            detail = resp.json().get("error") or resp.text
        except ValueError:
            detail = resp.text
        raise RuntimeError(f"回复服务返回 {resp.status_code}: {detail}")
    return resp.json().get("reply", "")


@function_tool
def generate_encouragement(text: str, reason: str = "") -> str:
    """调用鼓励进程，为用户生成鼓励回复。只用于积极情绪（label=positive）。"""
    return _call_reply(text, reason, "positive")


@function_tool
def generate_comfort(text: str, reason: str = "") -> str:
    """调用排解进程，为用户生成共情排解回复。只用于消极情绪（label=negative）。"""
    return _call_reply(text, reason, "negative")


# ----------------------------------------------------------------------
# Agents
# ----------------------------------------------------------------------
encourage_agent = Agent(
    name="Encourage",
    handoff_description="用户情绪积极时接手，生成鼓励回复。",
    model=common.LLM_MODEL,
    instructions="用户情绪是积极的。必须调用 generate_encouragement 工具生成回复，"
                 "然后把工具返回的正文作为你的最终回答，不要自己另写。",
    tools=[generate_encouragement],
)

comfort_agent = Agent(
    name="Comfort",
    handoff_description="用户情绪消极时接手，生成共情排解回复。",
    model=common.LLM_MODEL,
    instructions="用户情绪是消极的。必须调用 generate_comfort 工具生成回复，"
                 "然后把工具返回的正文作为你的最终回答，不要自己另写。",
    tools=[generate_comfort],
)

triage_agent = Agent(
    name="Triage",
    model=common.LLM_MODEL,
    instructions="""你是情绪疏导系统的分流器。
步骤：
1. 必须先调用 analyze_sentiment 工具，传入用户原话；不要凭空判断情绪。
2. 读取工具返回的 label：
   - label=positive → 立刻 handoff 给 Encourage
   - label=negative → 立刻 handoff 给 Comfort
3. 不要自己写回复，不要跳过 handoff，不要重复调用工具。""",
    tools=[analyze_sentiment],
    handoffs=[encourage_agent, comfort_agent],
)

app = Flask(__name__)


# ----------------------------------------------------------------------
# 单次编排：流式事件 + 最终结果（非流式复用同一逻辑）
# ----------------------------------------------------------------------
def _handoff_target(item) -> str:
    """从 handoff 事件里取目标 Agent 名：优先 target_agent，回退解析 transfer_to_X。"""
    target = getattr(item, "target_agent", None)
    if target is not None:
        return target.name
    raw_name = getattr(getattr(item, "raw_item", None), "name", "") or ""
    if raw_name.startswith("transfer_to_"):
        return raw_name[len("transfer_to_"):]
    return raw_name or "?"


async def _run(message: str, emit: Optional[Callable[[str, dict], None]]) -> Dict:
    start = time.time()
    trace: List[str] = []
    agents: List[str] = []
    sentiment: Dict = {}
    chunks: List[str] = []

    def push(kind: str, payload: dict) -> None:
        if emit:
            emit(kind, payload)

    result = Runner.run_streamed(triage_agent, message)
    async for ev in result.stream_events():
        # 1) 文本增量：只转发最终回答，过滤模型思考文本
        if ev.type == "raw_response_event":
            if getattr(ev.data, "type", "") == "response.output_text.delta":
                delta = getattr(ev.data, "delta", "") or ""
                if delta:
                    chunks.append(delta)
                    push("delta", {"text": delta})
            continue

        # 2) Agent 切换
        if ev.type == "agent_updated_stream_event":
            name = ev.new_agent.name
            if not agents or agents[-1] != name:
                agents.append(name)
                push("agent", {"name": name})
            continue

        # 3) 工具调用 / handoff
        if ev.type == "run_item_stream_event":
            if ev.name == "tool_called":
                tool_name = getattr(getattr(ev.item, "raw_item", None), "name", "") or "?"
                trace.append(tool_name)
                push("tool", {"name": tool_name})
            elif ev.name == "tool_output":
                output = getattr(ev.item, "output", None)
                if isinstance(output, str):
                    data = common.extract_json(output)
                    if data and "label" in data:
                        sentiment = data
            elif ev.name in ("handoff_requested", "handoff_occured"):
                target = _handoff_target(ev.item)
                if ev.name == "handoff_occured":
                    trace.append(f"handoff:{target}")
                    push("handoff", {"to": target})

    reply = str(result.final_output or "").strip()
    last_agent = result.last_agent.name if result.last_agent else ""
    mode = {"Encourage": "encourage", "Comfort": "comfort"}.get(last_agent)

    # 兜底：模型没 handoff，或没走进程2 的工具，就按已识别的 label 显式补跑
    used_reply_tool = any(t.startswith("generate_") for t in trace)
    if not mode or not used_reply_tool or not reply:
        label = sentiment.get("label", "negative")
        fallback_agent = encourage_agent if label == "positive" else comfort_agent
        if not sentiment:
            raise RuntimeError("未拿到情感识别结果，无法编排")
        r2 = await Runner.run(fallback_agent, message)
        reply = str(r2.final_output or "").strip()
        mode = "encourage" if label == "positive" else "comfort"
        trace.append("fallback")
        if not reply:
            raise RuntimeError("兜底分支未生成回复")

    return {
        "message": message,
        "sentiment": sentiment,
        "mode": mode,
        "reply": reply,
        "trace": trace,
        "agents": agents,
        "elapsed": round(time.time() - start, 2),
        "error": None,
    }


# ----------------------------------------------------------------------
# 路由
# ----------------------------------------------------------------------
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

    try:
        result = asyncio.run(_run(message, None))
    except Exception as e:
        return jsonify({"error": common.error_text(e)}), 502
    return jsonify(result)


@app.route("/api/chat/stream", methods=["POST"])
def chat_stream():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "请求体必须是 JSON 对象"}), 400
    message = common.clip_text(data.get("message", ""))
    if not message:
        return jsonify({"error": "message 不能为空"}), 400

    # Agent 跑在独立线程的事件循环里，事件通过队列回传给 SSE 生成器
    q: "queue.Queue" = queue.Queue()

    def worker() -> None:
        def emit(kind: str, payload: dict) -> None:
            q.put((kind, payload))

        try:
            result = asyncio.run(_run(message, emit))
            q.put(("done", result))
        except Exception as e:
            q.put(("error", {"error": common.error_text(e)}))
        finally:
            q.put(None)

    threading.Thread(target=worker, daemon=True, name="agent-run").start()

    def generate():
        while True:
            item = q.get()
            if item is None:
                break
            kind, payload = item
            yield f"event: {kind}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    return Response(generate(), mimetype="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    })


if __name__ == "__main__":
    print(f"[backend] :{common.BACKEND_PORT}  Agents=Triage→Handoff→Encourage/Comfort  模型={common.LLM_MODEL}")
    app.run(host=common.BIND_HOST, port=common.BACKEND_PORT, debug=False, threaded=True)
