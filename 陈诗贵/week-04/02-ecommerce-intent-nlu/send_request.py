"""命令行接口测试工具：发送中文请求到意图识别服务，避开 PowerShell 编码坑。

用法：
    python send_request.py                        # 默认文本 -> TF-IDF 接口
    python send_request.py "怎么退款"              # 指定文本 -> TF-IDF 接口
    python send_request.py "怎么退款" bert         # 指定文本 -> BERT 接口
"""
import json
import sys
import urllib.request

# 解决 Windows 控制台中文输出编码问题
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE = "http://127.0.0.1:8000"


def post(endpoint: str, text: str) -> dict:
    payload = {"request_id": "req-001", "request_text": text}
    req = urllib.request.Request(
        f"{BASE}{endpoint}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


if __name__ == "__main__":
    text = sys.argv[1] if len(sys.argv) > 1 else "怎么退货"
    model = sys.argv[2] if len(sys.argv) > 2 else "tfidf"
    endpoint = "/v1/intent/tfidf" if model == "tfidf" else "/v1/intent/bert"

    result = post(endpoint, text)
    print(json.dumps(result, ensure_ascii=False, indent=2))
