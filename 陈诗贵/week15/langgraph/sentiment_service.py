# -*- coding: utf-8 -*-
"""进程1 · 情感识别服务（端口 5002）

职责：接收文本，调用 LLM 判断 positive / negative。
只做分类，不生成回复——回复是进程2 的事。

启动：
    python sentiment_service.py
调试：
    curl -X POST http://127.0.0.1:5002/api/analyze -H "Content-Type: application/json" -d "{\"text\":\"今天升职了\"}"
"""
import re
import time

from flask import Flask, jsonify, request

import common

app = Flask(__name__)

SYSTEM_PROMPT = """你是中文情感分类器。判断用户这句话的整体情绪倾向，只能二选一：
- positive：整体积极、愉快、满意、期待、放松
- negative：整体消极、难过、愤怒、焦虑、疲惫；情绪中性、模糊、难以判断时也归 negative

严格只输出一个 JSON 对象，不要解释、不要代码块以外的文字：
{"label": "positive" 或 "negative", "score": 0到1之间的小数, "reason": "20字以内的判断依据"}"""


def analyze(text: str) -> dict:
    """调 LLM 做二分类，返回 {label, score, reason}。"""
    llm = common.get_llm(temperature=0)
    resp = llm.invoke([
        ("system", SYSTEM_PROMPT),
        ("user", text),
    ])
    raw = resp.content if isinstance(resp.content, str) else str(resp.content)
    data = common.extract_json(raw)
    if not data:
        raise RuntimeError(f"模型未返回合法 JSON：{raw[:120]}")

    label = str(data.get("label", "")).strip().lower()
    if label not in ("positive", "negative"):
        # 兜底：模型给了中性/其他标签时按约定归负，保证下游一定有分支可走
        label = "negative"
    try:
        score = float(data.get("score", 0.6))
    except (TypeError, ValueError):
        score = 0.6
    score = min(1.0, max(0.0, score))

    reason = str(data.get("reason", "")).strip()
    reason = re.sub(r"\s+", " ", reason)[:60]
    return {"label": label, "score": round(score, 2), "reason": reason or "模型未说明依据"}


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "sentiment", "has_key": common.has_api_key()})


@app.route("/api/analyze", methods=["POST"])
def analyze_api():
    if not common.has_api_key():
        return jsonify({"error": "未配置 DS_API_KEY，请检查 root/.env"}), 500

    data = request.get_json(silent=True) or {}
    text = common.clip_text(data.get("text", ""))
    if not text:
        return jsonify({"error": "text 不能为空"}), 400

    start = time.time()
    try:
        result = analyze(text)
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500

    result["elapsed"] = round(time.time() - start, 2)
    return jsonify(result)


if __name__ == "__main__":
    print(f"[sentiment] :{common.SENTIMENT_PORT}  模型={common.LLM_MODEL}  Key={'已配置' if common.has_api_key() else '未配置'}")
    app.run(host="127.0.0.1", port=common.SENTIMENT_PORT, debug=False)
