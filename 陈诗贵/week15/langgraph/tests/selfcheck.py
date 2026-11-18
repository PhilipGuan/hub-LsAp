# -*- coding: utf-8 -*-
"""断言式自检：覆盖 README 第 7 节中可自动判定的用例。

前置：先另开终端运行 `python run_all.py`（四进程都在）。
运行：python tests/selfcheck.py

不依赖测试框架，失败即打印并以非 0 退出。
"""
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common  # noqa: E402

SENTIMENT = f"http://127.0.0.1:{common.SENTIMENT_PORT}"
REPLY = f"http://127.0.0.1:{common.REPLY_PORT}"
BACKEND = f"http://127.0.0.1:{common.BACKEND_PORT}"
FRONTEND = f"http://127.0.0.1:{common.FRONTEND_PORT}"

passed, failed = [], []


def check(case_id: str, desc: str, fn):
    try:
        fn()
        passed.append(case_id)
        print(f"PASS  {case_id}  {desc}")
    except AssertionError as e:
        failed.append(case_id)
        print(f"FAIL  {case_id}  {desc}  → {e}")
    except Exception as e:  # 网络/解析异常也记为失败
        failed.append(case_id)
        print(f"FAIL  {case_id}  {desc}  → {type(e).__name__}: {e}")


def expect(cond, msg):
    if not cond:
        raise AssertionError(msg)


def post(url, json_body=None, raw=None):
    if raw is not None:
        return requests.post(url, data=raw, headers={"Content-Type": "text/plain"}, timeout=90)
    return requests.post(url, json=json_body, timeout=90)


# ---------------- 进程1 情感识别 ----------------
def s05_empty():
    r = post(f"{SENTIMENT}/api/analyze", {"text": ""})
    expect(r.status_code == 400, f"期望 400，实际 {r.status_code}")
    expect(r.json().get("error"), "缺少 error 字段")


def s08_structure():
    r = post(f"{SENTIMENT}/api/analyze", {"text": "今天终于升职了，太开心了"})
    expect(r.status_code == 200, f"期望 200，实际 {r.status_code}: {r.text[:120]}")
    d = r.json()
    expect(d.get("label") in ("positive", "negative"), f"label 非法: {d.get('label')}")
    expect(isinstance(d.get("score"), (int, float)) and 0 <= d["score"] <= 1, "score 不在 0~1")
    expect(d.get("reason"), "reason 为空")
    expect(d["label"] == "positive", f"明显正向文本被判为 {d['label']}")


def s03_neutral():
    r = post(f"{SENTIMENT}/api/analyze", {"text": "今天星期一"})
    expect(r.status_code == 200, f"期望 200，实际 {r.status_code}")
    expect(r.json()["label"] == "negative", "约定：中性/模糊归 negative")


def s06_long_text():
    r = post(f"{SENTIMENT}/api/analyze", {"text": "今天有点累。" * 350})
    expect(r.status_code == 200, f"超长文本期望 200，实际 {r.status_code}")


# ---------------- 进程2 共情回复 ----------------
def r03_bad_label():
    r = post(f"{REPLY}/api/reply", {"text": "随便", "label": "neutral"})
    expect(r.status_code == 400, f"期望 400，实际 {r.status_code}")


def r04_empty_text():
    r = post(f"{REPLY}/api/reply", {"text": "", "label": "positive"})
    expect(r.status_code == 400, f"期望 400，实际 {r.status_code}")


def r01_encourage():
    r = post(f"{REPLY}/api/reply", {"text": "我拿到 offer 了", "label": "positive", "reason": ""})
    expect(r.status_code == 200, f"期望 200，实际 {r.status_code}: {r.text[:120]}")
    d = r.json()
    expect(d.get("mode") == "encourage", f"mode 期望 encourage，实际 {d.get('mode')}")
    expect(len(d.get("reply", "")) >= 20, "回复过短")


# ---------------- 后端 LangGraph 编排 ----------------
def b06_empty_message():
    r = post(f"{BACKEND}/api/chat", {"message": ""})
    expect(r.status_code == 400, f"期望 400，实际 {r.status_code}")


def b07_non_json():
    r = post(f"{BACKEND}/api/chat", raw="hello")
    expect(r.status_code == 400, f"期望 400，实际 {r.status_code}")


def b01_positive_flow():
    r = post(f"{BACKEND}/api/chat", {"message": "我拿到 offer 了，特别开心"})
    expect(r.status_code == 200, f"期望 200，实际 {r.status_code}: {r.text[:150]}")
    d = r.json()
    expect(d.get("mode") == "encourage", f"mode 期望 encourage，实际 {d.get('mode')}")
    expect(d.get("trace") == ["node_sentiment", "node_encourage"], f"trace 异常: {d.get('trace')}")
    expect(d.get("reply"), "reply 为空")
    expect(d["sentiment"].get("label") == "positive", "端到端 label 不是 positive")


def b02_negative_flow():
    r = post(f"{BACKEND}/api/chat", {"message": "我被裁员了，真的很难受"})
    expect(r.status_code == 200, f"期望 200，实际 {r.status_code}: {r.text[:150]}")
    d = r.json()
    expect(d.get("mode") == "comfort", f"mode 期望 comfort，实际 {d.get('mode')}")
    expect(d.get("trace") == ["node_sentiment", "node_comfort"], f"trace 异常: {d.get('trace')}")


# ---------------- 前端 ----------------
def f01_page():
    r = requests.get(f"{FRONTEND}/", timeout=10)
    expect(r.status_code == 200, f"期望 200，实际 {r.status_code}")
    expect("情绪疏导" in r.text, "页面内容异常")


def f04_frontend_empty():
    r = post(f"{FRONTEND}/api/chat", {"message": ""})
    expect(r.status_code == 400, f"前端转发期望 400，实际 {r.status_code}")


def f02_frontend_forward():
    r = post(f"{FRONTEND}/api/chat", {"message": "今天和朋友吃了顿好的，心情不错"})
    expect(r.status_code == 200, f"期望 200，实际 {r.status_code}: {r.text[:150]}")
    expect(r.json().get("reply"), "前端转发后 reply 为空")


def main() -> int:
    print("=" * 62)
    print("自检开始（需要 run_all.py 已在运行）")
    print("=" * 62)

    if not common.has_api_key():
        print("[跳过] 未配置 DS_API_KEY，LLM 相关用例会失败，请先检查 root/.env")

    check("S-05", "情感识别 · 空文本返回 400", s05_empty)
    check("S-08", "情感识别 · 结构合法且正向判对", s08_structure)
    check("S-03", "情感识别 · 中性文本归 negative", s03_neutral)
    check("S-06", "情感识别 · 超长文本不崩溃", s06_long_text)
    check("R-03", "共情回复 · 非法 label 返回 400", r03_bad_label)
    check("R-04", "共情回复 · 空文本返回 400", r04_empty_text)
    check("R-01", "共情回复 · 正向走 encourage", r01_encourage)
    check("B-06", "后端 · 空消息返回 400", b06_empty_message)
    check("B-07", "后端 · 非 JSON 返回 400", b07_non_json)
    check("B-01", "后端 · 正向端到端", b01_positive_flow)
    check("B-02", "后端 · 负向端到端", b02_negative_flow)
    check("F-01", "前端 · 页面可访问", f01_page)
    check("F-04", "前端 · 空消息被拦截", f04_frontend_empty)
    check("F-02", "前端 · 转发链路可用", f02_frontend_forward)

    print("-" * 62)
    print(f"通过 {len(passed)} 项，失败 {len(failed)} 项")
    if failed:
        print("失败用例：" + ", ".join(failed))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
