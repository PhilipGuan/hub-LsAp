"""并发压测：10 并发 × 20 轮（`PRODUCTION.md` §5 验收项 4）。

判据是 §7 风险 1：FastAPI 的**同步**路由跑在线程池里，而 `_state["parser"]` 是跨线程共享的
单例，状态库连接也跨线程复用 —— 这套组合在本项目从没被并发验证过。

为什么不用 `TestClient`：它把请求投进同一个 event loop 的 portal，看不见真实的多线程并发。
本脚本**真的起一个 uvicorn**（`port=0`，让内核分配端口），再从 10 个线程各开一个会话打 20 轮，
测的就是部署形态本身：`uvicorn --workers 1` + 线程池 + 共享 SQLite（WAL）。

为什么模型侧是**假**的：这里要测的是并发与状态，不是模型延迟；假模型让结论**可离线重跑**。
真模型的成本另由 `scripts/cost_snapshot.py` 出具（§5 验收项 5）。

跑法（项目根目录，或任何目录——脚本会自己切过去）：

    /Users/philipclaw/Downloads/padow-ai/.venv/bin/python scripts/loadtest.py
    /Users/philipclaw/Downloads/padow-ai/.venv/bin/python scripts/loadtest.py --sessions 4 --rounds 5
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)                      # 让 `chinook.db` / `.env` 的逐级向上查找按预期命中

import httpx                       # noqa: E402
import uvicorn                     # noqa: E402

from server import app as server_app          # noqa: E402
from server import store as store_module      # noqa: E402
from sqlagent import llm                      # noqa: E402
from sqlagent.schemas import ScopeJudgement, TableSelection   # noqa: E402

QUESTION = "数据库里一共有多少首歌曲？"
SQL = "SELECT COUNT(*) FROM tracks;"
ANSWER = "共有 3503 首歌曲。"


# --------------------------------------------------------------------------- #
# 假模型（进程内替换 `sqlagent.llm` 两个入口，零网络）
# --------------------------------------------------------------------------- #

def install_fake_llm() -> None:
    """按 system 提示词认领角色：SQL 生成 / 追问改写 / 历史摘要 / 组织回答。

    比「按调用顺序发牌」的写法更经得起压测：并发下顺序本来就不可预期，而提示词是稳定的。
    """

    def structured(schema, messages, **kwargs):
        if schema is ScopeJudgement:
            return ScopeJudgement(scope="in", reason="压测：统计歌曲", tables=["tracks"])
        if schema is TableSelection:
            return TableSelection(tables=["tracks"])
        return None

    def text(messages, **kwargs):
        system = llm.content_to_text(messages[0].content)
        if "SQLite 专家" in system:
            return SQL
        if "查询改写助手" in system:
            return QUESTION
        if "对话压缩助手" in system:
            return ""
        return ANSWER

    llm.invoke_structured_messages = structured
    llm.invoke_messages = text


# --------------------------------------------------------------------------- #
# 日志采集：从结构化日志里数「炸没炸」
# --------------------------------------------------------------------------- #

class _Collector(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[dict] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.records.append(json.loads(record.getMessage()))
        except Exception:                       # noqa: BLE001 —— 日志采集绝不能反过来搞挂压测
            pass

    def count(self, event: str) -> int:
        return sum(1 for r in self.records if r.get("event") == event)

    @property
    def locked(self) -> int:
        """结构化日志里出现 `database is locked` 的次数（§7 风险 4 的直接判据）。"""
        return sum(1 for r in self.records if "database is locked" in str(r).lower())


def _start_server(port: int) -> tuple[uvicorn.Server, str]:
    """起一个真 uvicorn（线程内），返回 `(server, base_url)`。"""
    config = uvicorn.Config(server_app.app, host="127.0.0.1", port=port,
                            log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, name="uvicorn", daemon=True).start()

    deadline = time.time() + 30
    while not server.started:
        if time.time() > deadline:
            raise RuntimeError("uvicorn 启动超时")
        time.sleep(0.05)
    actual_port = server.servers[0].sockets[0].getsockname()[1]
    return server, f"http://127.0.0.1:{actual_port}"


def _session_worker(base: str, rounds: int, latencies: list, failures: list,
                    statuses: dict) -> str:
    """一个会话打 `rounds` 轮（串行——同一会话本就该是串行的，并发发生在**会话之间**）。"""
    with httpx.Client(base_url=base, timeout=120.0) as c:
        sid = c.post("/api/session", json={"route": "graph", "human_review": False}).json()["session_id"]
        for i in range(rounds):
            t0 = time.perf_counter()
            r = c.post("/api/chat", json={"session_id": sid, "message": QUESTION})
            latencies.append((time.perf_counter() - t0) * 1000)
            statuses[r.status_code] = statuses.get(r.status_code, 0) + 1
            if r.status_code != 200 or not r.json().get("trace", {}).get("rows"):
                failures.append((sid, i, r.status_code, r.text[:200]))
    return sid


def _poll_health(base: str, stop: threading.Event, results: list) -> None:
    """并发期间持续探 `/health`：它带「状态库可写性」探测，正好顺带压一压这个探针。"""
    with httpx.Client(base_url=base, timeout=30.0) as c:
        while not stop.is_set():
            try:
                results.append(bool(c.get("/health").json().get("ok")))
            except Exception:                   # noqa: BLE001
                results.append(False)
            time.sleep(0.05)


def _audit_count(state_db: Path) -> int:
    import sqlite3

    conn = sqlite3.connect(state_db)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM audit").fetchone()[0])
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="10 并发 × 20 轮压测（离线，假模型）")
    parser.add_argument("--sessions", type=int, default=10, help="并发会话数（默认 10）")
    parser.add_argument("--rounds", type=int, default=20, help="每个会话的轮数（默认 20）")
    parser.add_argument("--port", type=int, default=0, help="监听端口，0 = 由内核分配")
    args = parser.parse_args(argv)

    total = args.sessions * args.rounds
    state_db = Path(tempfile.mkdtemp(prefix="loadtest-")) / "state.sqlite"
    os.environ["APP_PASSCODE"] = ""             # 准入已由单测覆盖，这里专注并发与状态
    os.environ["STATE_DB_PATH"] = str(state_db)

    install_fake_llm()
    collector = _Collector()
    logging.getLogger("sqlagent.server").addHandler(collector)

    server, base = _start_server(args.port)
    print(f"会话数 {args.sessions} × 每会话 {args.rounds} 轮 = {total} 轮；服务 {base}")
    print(f"状态库 {state_db}（WAL + busy_timeout=5000）")

    latencies: list[float] = []
    failures: list[tuple] = []
    statuses: dict[int, int] = {}
    health: list[bool] = []
    stop = threading.Event()
    poller = threading.Thread(target=_poll_health, args=(base, stop, health), daemon=True)
    poller.start()

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.sessions) as pool:
        list(pool.map(lambda _: _session_worker(base, args.rounds, latencies, failures, statuses),
                      range(args.sessions)))
    wall = time.perf_counter() - started
    stop.set()
    poller.join(timeout=5)

    extra = None
    if total == store_module.GLOBAL_DAILY_ROUNDS:
        # 额度恰好卡在边界上：第 201 轮必须被拦下（并发下也不能多发一格）
        with httpx.Client(base_url=base, timeout=60.0) as c:
            sid = c.post("/api/session", json={"route": "graph", "human_review": False}).json()["session_id"]
            r = c.post("/api/chat", json={"session_id": sid, "message": QUESTION})
            extra = (r.status_code, r.json().get("detail", "")[:40])

    server.should_exit = True
    time.sleep(0.3)

    audit_rows = _audit_count(state_db)
    ordered = sorted(latencies)

    def pct(p: float) -> float:
        return ordered[min(len(ordered) - 1, int(len(ordered) * p))]

    print("\n—— 结果 ——")
    print(f"总轮数 {total}；成功 {total - len(failures)}；失败 {len(failures)}；"
          f"HTTP 状态分布 {statuses}")
    print(f"墙钟 {wall:.2f}s；吞吐 {total / wall:.1f} 轮/秒")
    print(f"单轮延迟 P50 {pct(0.5):.0f}ms / P95 {pct(0.95):.0f}ms / max {ordered[-1]:.0f}ms")
    print(f"/health 探测 {len(health)} 次，非 ok {len(health) - sum(health)} 次")
    print(f"审计表行数 {audit_rows}（应等于总轮数）")
    print(f"日志里 `database is locked` {collector.locked} 次；"
          f"`unhandled` {collector.count('unhandled')} 次；`llm_error` {collector.count('llm_error')} 次")
    if extra is not None:
        print(f"额度边界：第 {total + 1} 轮 → HTTP {extra[0]}：{extra[1]}")

    if failures:
        print("\n失败样例：")
        for f in failures[:5]:
            print(f"  {f}")
    ok = (not failures and audit_rows == total and all(health)
          and collector.locked == 0 and collector.count("unhandled") == 0)
    print("\n结论：" + ("通过（并发下无锁冲突、无 5xx、审计逐轮落表）" if ok else "不通过"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
