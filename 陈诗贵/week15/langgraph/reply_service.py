# -*- coding: utf-8 -*-
"""进程2 · 共情回复服务（端口 5003）

职责：拿到情感识别结果后生成回复。
- label=positive → 鼓励：肯定具体的事，放大积极面
- label=negative → 排解：先共情命名情绪，再给 1~2 条可执行小建议

启动：
    python reply_service.py
调试：
    curl -X POST http://127.0.0.1:5003/api/reply -H "Content-Type: application/json" -d "{\"text\":\"我被裁员了\",\"label\":\"negative\",\"reason\":\"失落\"}"
"""
import time

from flask import Flask, jsonify, request

import common

app = Flask(__name__)

PROMPTS = {
    "positive": """你是一位真诚、不油腻的朋友。用户刚刚表达了积极情绪，请给予鼓励。
要求：
1. 先具体肯定用户提到的事，不要泛泛夸；
2. 帮用户把这份积极感受放大一点，或指出它背后的努力；
3. 结尾给一句轻量的行动建议或祝福；
4. 80~200 字，口语化，不要说教，不要用「这没什么」「想开点」这类否定情绪的话；
5. 只输出回复正文，不要标题、不要分点、不要 emoji 堆砌。""",
    "negative": """你是一位有共情力的倾听者。用户刚刚表达了负面情绪，请在回复中排解。
要求：
1. 第一句先承接情绪：准确命名并允许这种感受存在，不要急着讲道理；
2. 再温和地帮用户把处境说得更清楚一点，让他感到被理解；
3. 最后给 1~2 条今天就能做的小建议（具体、低成本，不说「多喝热水」这类空话）；
4. 80~250 字，口语化，不要说教，不要诊断（避免「你这是抑郁」），不要强行正能量；
5. 只输出回复正文，不要标题、不要分点、不要 emoji 堆砌。""",
}


def build_reply(text: str, label: str, reason: str) -> str:
    llm = common.get_llm(temperature=0.7)
    context = f"用户原话：{text}"
    if reason:
        context += f"\n情感判断依据：{reason}"
    resp = llm.invoke([
        ("system", PROMPTS[label]),
        ("user", context),
    ])
    return (resp.content if isinstance(resp.content, str) else str(resp.content)).strip()


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "reply", "has_key": common.has_api_key()})


@app.route("/api/reply", methods=["POST"])
def reply_api():
    if not common.has_api_key():
        return jsonify({"error": "未配置 DS_API_KEY，请检查 root/.env"}), 500

    data = request.get_json(silent=True) or {}
    text = common.clip_text(data.get("text", ""))
    label = str(data.get("label", "")).strip().lower()
    if not text:
        return jsonify({"error": "text 不能为空"}), 400
    if label not in PROMPTS:
        return jsonify({"error": "label 只能是 positive 或 negative"}), 400

    start = time.time()
    try:
        reply = build_reply(text, label, str(data.get("reason", "")).strip())
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500
    if not reply:
        return jsonify({"error": "模型返回了空回复"}), 500

    return jsonify({
        "mode": "encourage" if label == "positive" else "comfort",
        "reply": reply,
        "elapsed": round(time.time() - start, 2),
    })


if __name__ == "__main__":
    print(f"[reply] :{common.REPLY_PORT}  模型={common.LLM_MODEL}  Key={'已配置' if common.has_api_key() else '未配置'}")
    app.run(host="127.0.0.1", port=common.REPLY_PORT, debug=False)
