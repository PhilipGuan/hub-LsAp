"""成本快照：5 轮典型对话的 token / 耗时 / 是否降级（`PRODUCTION.md` §5 验收项 5）。

与 `loadtest.py` 刚好相反：那一份**必须离线**（测并发与状态），这一份**必须用真模型**——
它要回答的是「这条链接被面试官点开、问上 5 轮，实际花掉多少 token」。

token 从哪来：`ChatOpenAI` 的响应里带 `usage_metadata`。这里在**进程内**把类方法包一层
把它捞出来，`sqlagent/` 与 `server/` 一行都不改（D4）。走的是真 HTTP + 真边界层，
所以顺带验证了整条链路（检查点 / 审计 / envelope）在真模型下的表现。

跑法（项目根目录，或任何目录——脚本会自己切过去）：

    /Users/philipclaw/Downloads/padow-ai/.venv/bin/python scripts/cost_snapshot.py
    # 想直接看金额，就把 provider 官网的单价填进来（元/百万 token）：
    /Users/philipclaw/Downloads/padow-ai/.venv/bin/python scripts/cost_snapshot.py --price-in 2 --price-out 8
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)                      # 让 `.env` 与 `chinook.db` 的逐级向上查找按预期命中

import httpx                       # noqa: E402
import uvicorn                     # noqa: E402
from langchain_openai import ChatOpenAI        # noqa: E402

from server import app as server_app           # noqa: E402

# 5 轮典型对话：都落在 chinook 范围内，覆盖「小表聚合 / 带排序取数 / 分组统计」几类常见形态。
QUESTIONS = [
    "数据库里一共有多少首歌曲？",
    "时长最长的 5 首歌分别是哪些？",
    "哪个国家的客户数量最多？",
    "每个流派的曲目数量分别是多少？",
    "2021 年一共开了多少张发票？",
]

_usage = {"calls": 0, "input": 0, "output": 0}


def install_usage_counter() -> None:
    """把 `ChatOpenAI.invoke` 包一层数 token（两条路线、结构化与非结构化都会经过它）。"""
    original = ChatOpenAI.invoke

    def counting(self, *args, **kwargs):
        response = original(self, *args, **kwargs)
        usage = getattr(response, "usage_metadata", None) or {}
        _usage["calls"] += 1
        _usage["input"] += int(usage.get("input_tokens") or 0)
        _usage["output"] += int(usage.get("output_tokens") or 0)
        return response

    ChatOpenAI.invoke = counting


def _reset_usage() -> dict:
    snapshot = dict(_usage)
    _usage.update(calls=0, input=0, output=0)
    return snapshot


def _start_server(port: int) -> tuple[uvicorn.Server, str]:
    """起一个真 uvicorn（线程内）——与 `loadtest.py` 同一手法，各自独立不共享代码。"""
    config = uvicorn.Config(server_app.app, host="127.0.0.1", port=port,
                            log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, name="uvicorn", daemon=True).start()
    deadline = time.time() + 30
    while not server.started:
        if time.time() > deadline:
            raise RuntimeError("uvicorn 启动超时")
        time.sleep(0.05)
    return server, f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="5 轮典型对话的 token / 耗时 / 降级快照（真模型）")
    parser.add_argument("--rounds", type=int, default=len(QUESTIONS))
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--price-in", type=float, default=None, help="输入单价（元 / 百万 token）")
    parser.add_argument("--price-out", type=float, default=None, help="输出单价（元 / 百万 token）")
    args = parser.parse_args(argv)

    state_db = Path(tempfile.mkdtemp(prefix="cost-snapshot-")) / "state.sqlite"
    os.environ["APP_PASSCODE"] = ""             # 快照量的是对话成本，不是准入
    os.environ["STATE_DB_PATH"] = str(state_db)
    install_usage_counter()

    server, base = _start_server(args.port)
    print(f"服务 {base}；状态库 {state_db}")
    print(f"模型由 .env 决定（provider = {os.environ.get('LLM_PROVIDER', 'deepseek')}）\n")

    rows = []
    failed = False
    with httpx.Client(base_url=base, timeout=120.0) as c:
        sid = c.post("/api/session", json={"route": "graph", "human_review": False}).json()["session_id"]
        for i, question in enumerate(QUESTIONS[:args.rounds], start=1):
            _reset_usage()
            t0 = time.perf_counter()
            r = c.post("/api/chat", json={"session_id": sid, "message": question})
            elapsed = (time.perf_counter() - t0) * 1000
            used = _reset_usage()

            if r.status_code != 200:
                failed = True
                rows.append((i, question, 0, 0, 0, elapsed, f"HTTP {r.status_code}", None))
                print(f"[{i}] HTTP {r.status_code}：{r.text[:160]}")
                continue

            body = r.json()
            trace = body.get("trace") or {}
            data_rows = trace.get("rows")
            rows.append((i, question, used["calls"], used["input"], used["output"], elapsed,
                         ("降级" if body.get("degraded") else body.get("status")),
                         trace.get("answer")))
            print(f"[{i}] {question}\n"
                  f"    LLM {used['calls']} 次 · in {used['input']} / out {used['output']} tokens · "
                  f"{elapsed:.0f}ms · {rows[-1][6]} · 结果 {len(data_rows) if data_rows else 0} 行\n"
                  f"    SQL: {trace.get('sql')}")

    server.should_exit = True
    time.sleep(0.3)

    print("\n—— 汇总 ——")
    print("| 轮 | LLM 调用 | 输入 token | 输出 token | 耗时 ms | 状态 |")
    print("|---|---|---|---|---|---|")
    for i, _q, calls, tin, tout, elapsed, status, _ans in rows:
        print(f"| {i} | {calls} | {tin} | {tout} | {elapsed:.0f} | {status} |")
    total_in = sum(r[3] for r in rows)
    total_out = sum(r[4] for r in rows)
    total_ms = sum(r[5] for r in rows)
    print(f"\n合计：{len(rows)} 轮 · LLM {sum(r[2] for r in rows)} 次 · "
          f"输入 {total_in} / 输出 {total_out} tokens · 服务端累计耗时 {total_ms:.0f}ms")
    if args.price_in is not None or args.price_out is not None:
        cost = (total_in / 1e6) * (args.price_in or 0) + (total_out / 1e6) * (args.price_out or 0)
        print(f"按输入 {args.price_in} 元/百万 + 输出 {args.price_out} 元/百万 计：**{cost:.4f} 元**")
    print(f"\n状态库：{state_db}（跑完可删；`audit` 表里每轮一行，含服务端耗时）")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
