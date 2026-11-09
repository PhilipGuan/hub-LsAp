# -*- coding: utf-8 -*-
"""
消防楼道风险识别 — Flask 后端
接收前端上传的图片，调用 deepseek-flash 视觉接口，返回结构化 JSON 结果。
"""
import os
import json
import re
import base64
import requests
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__, static_folder="static", static_url_path="")

# ------- 配置 -------
DS_API_KEY = os.getenv("DS_API_KEY", "   ")
DS_API_URL = "https://api.deepseek.com/v1/chat/completions"
DS_MODEL = "deepseek-flash"

# Ponytail: 硬编码 API key 已预留环境变量覆盖口；生产请务必设置 DS_API_KEY 环境变量

SYSTEM_PROMPT = """你是一位经验丰富的消防安全检查员，擅长根据楼道场景照片判断消防安全隐患等级。

请严格按照以下标准对每张图片分类：
- 高风险：楼道中出现电动车、电瓶、飞线充电等可能起火的元素。
- 中风险：楼道中存在大量堆积物严重影响通行或堆放大量纸箱、木质家具等能造成火势蔓延的堵塞物。
- 低风险：存在楼道堆物现象但不严重。
- 无风险：楼道干净，无堆放物品。
- 非楼道：一些与楼道无关的图片。

**必须**以严格的 JSON 格式输出，不要输出任何其他文字、Markdown 代码块或解释。JSON 格式如下：
{"level":"高风险|中风险|低风险|无风险|非楼道","confidence":0-100的整数,"reason":"一句话说明判断依据","details":["具体隐患点1","具体隐患点2"]}"""

# ------- 路由 -------
@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/detect", methods=["POST"])
def detect():
    # 接收方式 1：multipart/form-data 上传图片
    if "image" in request.files:
        file = request.files["image"]
        mime = file.mimetype or "image/jpeg"
        img_bytes = file.read()
    # 接收方式 2：JSON 里带 base64
    elif request.is_json:
        data = request.get_json()
        b64 = data.get("image", "")
        mime = data.get("mime", "image/jpeg")
        # 允许带或不带 data:image/xxx;base64, 前缀
        if "," in b64:
            _, b64 = b64.split(",", 1)
        try:
            img_bytes = base64.b64decode(b64)
        except Exception as e:
            return jsonify({"error": f"base64 解码失败: {e}"}), 400
    else:
        return jsonify({"error": "请上传图片文件或 JSON (image_base64)"}), 400

    if not img_bytes:
        return jsonify({"error": "图片内容为空"}), 400

    b64_data = base64.b64encode(img_bytes).decode("ascii")
    img_url = f"data:{mime};base64,{b64_data}"

    # 调 DeepSeek API
    try:
        resp = requests.post(
            DS_API_URL,
            headers={
                "Authorization": f"Bearer {DS_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": DS_MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": img_url}},
                        {"type": "text", "text": "请按标准 JSON 格式判断这张楼道照片的消防隐患等级。"},
                    ]},
                ],
                "max_tokens": 800,
                "temperature": 0.1,
            },
            timeout=60,
            proxies={"http": None, "https": None},  # 绕过系统代理
        )
    except Exception as e:
        return jsonify({"error": f"API 请求失败: {e}"}), 502

    if resp.status_code != 200:
        return jsonify({"error": f"API 返回 {resp.status_code}", "detail": resp.text[:500]}), 502

    data = resp.json()
    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")

    # 解析 JSON：大模型有时会包 ```json ... ```，剥掉
    m = re.search(r"```json\s*(\{.*?\})\s*```", content, re.S)
    if m:
        content = m.group(1)
    m = re.search(r"(\{.*\})", content, re.S)
    if m:
        content = m.group(1)

    try:
        result = json.loads(content)
    except json.JSONDecodeError:
        result = {"level": "解析失败", "confidence": 0, "reason": content, "details": []}

    result["raw_text"] = data.get("choices", [{}])[0].get("message", {}).get("reasoning_content", "")
    result["usage"] = data.get("usage", {})
    return jsonify(result)


if __name__ == "__main__":
    # 0.0.0.0 让手机也能访问（同一 WiFi）
    app.run(host="0.0.0.0", port=5000, debug=False)
