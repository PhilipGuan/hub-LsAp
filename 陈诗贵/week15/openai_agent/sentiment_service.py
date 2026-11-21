# -*- coding: utf-8 -*-
"""进程1 · 情感识别服务（端口 5012，OpenAI Agents SDK 实现）

SentimentAgent 只做二分类，输出 JSON 文本，服务端解析校验。
（DeepSeek 不支持 output_type 的 response_format，探针实测 400，因此走 JSON 文本解析。）

启动：
    python sentiment_service.py
调试：
    curl -X POST http://127.0.0.1:5012/api/analyze -H "Content-Type: application/json" -d "{\"text\":\"今天升职了\"}"
"""
import asyncio
import re
import time

from flask import Flask, jsonify, request

import common

common.init_sdk()

from agents import Agent, Runner  # noqa: E402  （必须在 init_sdk 之后导入使用）

sentiment_agent = Agent(
    name="SentimentAgent",
    model=common.LLM_MODEL,
    instructions="""你是中文情感分类器。判断用户这句话的整体情绪倾向，只能二选一：
- positive：整体积极、愉快、满意、期待、放松
- negative：整体消极、难过、愤怒、焦虑、疲惫；情绪中性、模糊、难以判断时也归 negative

严格只输出一个 JSON 对象，不要解释、不要代码块以外的文字：
{"label": "positive" 或 "negative", "score": 0到1之间的小数, "reason": "20字以内的判断依据"}""",
)

app = Flask(__name__)


def _parse(raw: str) -> dict:
    data = common.extract_json(raw)
    if not data:
        raise RuntimeError(f"模型未返回合法 JSON：{raw[:120]}")

    label = str(data.get("label", "")).strip().lower()
    if label not in ("positive", "negative"):
        # 模型给了中性/其他标签时按约定归负，保证下游一定有分支可走
        label = "negative"
    try:
        score = float(data.get("score", 0.6))
    except (TypeError, ValueError):
        score = 0.6
    score = min(1.0, max(0.0, score))

    reason = re.sub(r"\s+", " ", str(data.get("reason", ""))).strip()[:60]
    return {"label": label, "score": round(score, 2), "reason": reason or "模型未说明依据"}


async def _analyze_once(text: str) -> dict:
    result = await Runner.run(sentiment_agent, text)
    return _parse(str(result.final_output or ""))


def analyze(text: str) -> dict:
    """跑一次 Agent；JSON 解析失败再重试一次。"""
    try:
        return asyncio.run(_analyze_once(text))
    except RuntimeError:
        return asyncio.run(_analyze_once(text))


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
        return jsonify({"error": common.error_text(e)}), 500

    result["elapsed"] = round(time.time() - start, 2)
    return jsonify(result)


if __name__ == "__main__":
    print(f"[sentiment] :{common.SENTIMENT_PORT}  Agent=SentimentAgent  模型={common.LLM_MODEL}")
    app.run(host=common.BIND_HOST, port=common.SENTIMENT_PORT, debug=False)
