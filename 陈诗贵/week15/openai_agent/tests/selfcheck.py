# -*- coding: utf-8 -*-
"""断言式自检：覆盖 README 第 7 节中可自动判定的用例（含 SSE 流式）。

前置：先另开终端运行 `python run_all.py`（四进程都在）。
运行：python tests/selfcheck.py

不依赖测试框架，失败即打印并以非 0 退出。
"""
import codecs
import json
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
    except Exception as e:
        failed.append(case_id)
        print(f"FAIL  {case_id}  {desc}  → {type(e).__name__}: {e}")


def expect(cond, msg):
    if not cond:
        raise AssertionError(msg)


def post(url, json_body=None, raw=None):
    if raw is not None:
        return requests.post(url, data=raw, headers={"Content-Type": "text/plain"}, timeout=common.RPC_TIMEOUT)
    return requests.post(url, json=json_body, timeout=common.RPC_TIMEOUT)


def read_sse(url: str, payload: dict):
    """读完整条 SSE 流，返回 [(event, data), ...]；非 200 返回 (status, [])。"""
    events = []
    with requests.post(url, json=payload, stream=True, timeout=common.RPC_TIMEOUT) as resp:
        if resp.status_code != 200:
            return resp.status_code, events
        decoder = codecs.getincrementaldecoder("utf-8")()
        buf = ""
        for chunk in resp.iter_content(chunk_size=None):
            if not chunk:
                continue
            buf += decoder.decode(chunk)
            while "\n\n" in buf:
                raw, buf = buf.split("\n\n", 1)
                kind, data_lines = None, []
                for line in raw.split("\n"):
                    if line.startswith("event:"):
                        kind = line[6:].strip()
                    elif line.startswith("data:"):
                        data_lines.append(line[5:].strip())
                if kind:
                    body = "".join(data_lines)
                    events.append((kind, json.loads(body) if body else {}))
    return 200, events


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
    expect(d.get("agent") == "EncourageAgent", f"agent 期望 EncourageAgent，实际 {d.get('agent')}")
    expect(len(d.get("reply", "")) >= 20, "回复过短")


def r02_comfort():
    r = post(f"{REPLY}/api/reply", {"text": "我被裁员了", "label": "negative", "reason": "失落"})
    expect(r.status_code == 200, f"期望 200，实际 {r.status_code}: {r.text[:120]}")
    d = r.json()
    expect(d.get("mode") == "comfort", f"mode 期望 comfort，实际 {d.get('mode')}")
    expect(d.get("agent") == "ComfortAgent", f"agent 期望 ComfortAgent，实际 {d.get('agent')}")
    expect(len(d.get("reply", "")) >= 20, "回复过短")


# ---------------- 后端编排 ----------------
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
    expect("analyze_sentiment" in d.get("trace", []), f"trace 缺少 analyze_sentiment: {d.get('trace')}")
    expect(d.get("reply"), "reply 为空")
    expect(d["sentiment"].get("label") == "positive", "端到端 label 不是 positive")


def b02_negative_flow():
    r = post(f"{BACKEND}/api/chat", {"message": "我被裁员了，真的很难受"})
    expect(r.status_code == 200, f"期望 200，实际 {r.status_code}: {r.text[:150]}")
    d = r.json()
    expect(d.get("mode") == "comfort", f"mode 期望 comfort，实际 {d.get('mode')}")
    expect("analyze_sentiment" in d.get("trace", []), f"trace 缺少 analyze_sentiment: {d.get('trace')}")


# ---------------- SSE 流式 ----------------
def t01_stream_events():
    status, events = read_sse(f"{BACKEND}/api/chat/stream", {"message": "今天面试通过了，好开心"})
    expect(status == 200, f"期望 200，实际 {status}")
    kinds = [k for k, _ in events]
    expect("agent" in kinds, f"缺少 agent 事件: {kinds}")
    expect("tool" in kinds, f"缺少 tool 事件: {kinds}")
    expect("delta" in kinds, f"缺少 delta 事件: {kinds}")
    expect(kinds[-1] == "done", f"最后一个事件应为 done，实际 {kinds[-1]}")


def t02_delta_equals_reply():
    status, events = read_sse(f"{BACKEND}/api/chat/stream", {"message": "今天面试通过了，好开心"})
    expect(status == 200, f"期望 200，实际 {status}")
    text = "".join(d.get("text", "") for k, d in events if k == "delta")
    done = [d for k, d in events if k == "done"][0]
    expect(text, "没有收到任何 delta 文本")
    expect(text.strip() == done["reply"].strip(), "delta 拼接与 done.reply 不一致")


def t03_no_reasoning_event():
    status, events = read_sse(f"{BACKEND}/api/chat/stream", {"message": "项目又延期了，很崩溃"})
    expect(status == 200, f"期望 200，实际 {status}")
    kinds = {k for k, _ in events}
    expect(kinds <= {"agent", "tool", "handoff", "delta", "done", "error"}, f"出现未知事件类型: {kinds}")


def t05_stream_empty():
    r = post(f"{BACKEND}/api/chat/stream", {"message": ""})
    expect(r.status_code == 400, f"期望 400，实际 {r.status_code}")


# ---------------- 前端 ----------------
def f01_page():
    r = requests.get(f"{FRONTEND}/", timeout=10)
    expect(r.status_code == 200, f"期望 200，实际 {r.status_code}")
    expect("情绪疏导" in r.text, "页面内容异常")


def f02_frontend_stream():
    status, events = read_sse(f"{FRONTEND}/api/chat/stream", {"message": "今天和朋友吃了顿好的，心情不错"})
    expect(status == 200, f"期望 200，实际 {status}")
    expect(any(k == "done" for k, _ in events), "前端转发未收到 done 事件")


def f04_frontend_empty():
    r = post(f"{FRONTEND}/api/chat", {"message": ""})
    expect(r.status_code == 400, f"前端转发期望 400，实际 {r.status_code}")


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
    check("R-01", "共情回复 · 正向走 EncourageAgent", r01_encourage)
    check("R-02", "共情回复 · 负向走 ComfortAgent", r02_comfort)
    check("B-06", "后端 · 空消息返回 400", b06_empty_message)
    check("B-07", "后端 · 非 JSON 返回 400", b07_non_json)
    check("B-01", "后端 · 正向端到端", b01_positive_flow)
    check("B-02", "后端 · 负向端到端", b02_negative_flow)
    check("T-01", "SSE · 事件序列完整", t01_stream_events)
    check("T-02", "SSE · delta 拼接等于 done.reply", t02_delta_equals_reply)
    check("T-03", "SSE · 无 reasoning 事件", t03_no_reasoning_event)
    check("T-05", "SSE · 空消息返回 400", t05_stream_empty)
    check("F-01", "前端 · 页面可访问", f01_page)
    check("F-02", "前端 · SSE 转发链路可用", f02_frontend_stream)
    check("F-04", "前端 · 空消息被拦截", f04_frontend_empty)

    print("-" * 62)
    print(f"通过 {len(passed)} 项，失败 {len(failed)} 项")
    if failed:
        print("失败用例：" + ", ".join(failed))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
