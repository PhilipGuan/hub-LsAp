"""
程序一：用 Redis 存储历史对话（会话级多轮上下文）

用到的 Redis 数据结构
--------------------------------------------------------------------
  LIST  chat:session:{sid}:messages   对话流水，JSON 串按时间顺序 RPUSH
  HASH  chat:session:{sid}:meta       会话元信息（创建/更新时间、轮数等）
  ZSET  chat:sessions                 会话索引，score = 最后活跃时间戳
  TTL   expire                        每个会话 7 天不活跃自动回收
  SCAN  scan_iter                     演示如何遍历/统计键

运行方式
--------------------------------------------------------------------
  python redis_chat_history.py               # 交互式对话
  python redis_chat_history.py --demo        # 自动跑一段脚本化的对话
  python redis_chat_history.py --session s1  # 指定会话 ID
  python redis_chat_history.py --inspect     # 只查看 Redis 里已有的数据

交互命令
--------------------------------------------------------------------
  /history            查看当前会话历史
  /sessions           列出所有会话（按活跃时间倒序）
  /switch <sid>       切换会话
  /new                新建会话
  /clear              清空当前会话
  /keys               查看本次示例写入的 Redis 键及其类型
  /raw                查看底层 Redis 存储的原始结构
  /help  /quit
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid

import common
from common import bot_reply, now_ts

KEY_MSG = "chat:session:{sid}:messages"
KEY_META = "chat:session:{sid}:meta"
KEY_INDEX = "chat:sessions"
KEY_PREFIX = "chat:"

TTL_SECONDS = 7 * 24 * 3600   # 会话 7 天不活跃自动过期
MAX_MESSAGES = 200            # 单会话最多保留的消息条数（LTRIM 控制）


class RedisChatHistory:
    """基于 Redis 的会话历史存储。"""

    def __init__(self, client, user: str = "demo-user",
                 ttl: int = TTL_SECONDS, max_messages: int = MAX_MESSAGES):
        self.r = client
        self.user = user
        self.ttl = ttl
        self.max_messages = max_messages

    # ---------------- 键名 ----------------
    @staticmethod
    def _k_msg(sid: str) -> str:
        return KEY_MSG.format(sid=sid)

    @staticmethod
    def _k_meta(sid: str) -> str:
        return KEY_META.format(sid=sid)

    # ---------------- 写入 ----------------
    def append(self, sid: str, role: str, content: str) -> dict:
        """追加一条消息，并更新会话元信息与索引（一个事务里完成）。"""
        msg = {"role": role, "content": content, "ts": now_ts(), "user": self.user}
        payload = json.dumps(msg, ensure_ascii=False)

        k_msg, k_meta = self._k_msg(sid), self._k_meta(sid)
        now = time.time()

        pipe = self.r.pipeline(transaction=True)
        pipe.rpush(k_msg, payload)
        # 只保留最近 MAX_MESSAGES 条，避免列表无限增长
        pipe.ltrim(k_msg, -self.max_messages, -1)
        pipe.hset(k_meta, mapping={
            "session_id": sid,
            "user": self.user,
            "created_at": self.r.hget(k_meta, "created_at") or now_ts(),
            "updated_at": now_ts(),
        })
        pipe.hincrby(k_meta, "messages", 1)
        pipe.zadd(KEY_INDEX, {sid: now})
        pipe.expire(k_msg, self.ttl)
        pipe.expire(k_meta, self.ttl)
        pipe.execute()
        return msg

    # ---------------- 读取 ----------------
    def history(self, sid: str, limit: int = 0) -> list[dict]:
        """取会话历史；limit>0 时只取最近 limit 条。"""
        key = self._k_msg(sid)
        raw = self.r.lrange(key, -limit, -1) if limit > 0 else self.r.lrange(key, 0, -1)
        return [json.loads(x) for x in raw]

    def turns(self, sid: str) -> int:
        """会话轮数（用户消息条数）。"""
        return sum(1 for m in self.history(sid) if m.get("role") == "user")

    def meta(self, sid: str) -> dict:
        return self.r.hgetall(self._k_meta(sid))

    def sessions(self, limit: int = 20) -> list[dict]:
        """按最后活跃时间倒序列出会话，顺便清理已过期的索引项。"""
        out = []
        for sid, score in self.r.zrevrange(KEY_INDEX, 0, limit - 1, withscores=True):
            m = self.r.hgetall(self._k_meta(sid))
            if not m:                       # 元信息已过期 -> 索引一起清掉
                self.r.zrem(KEY_INDEX, sid)
                continue
            out.append({"session_id": sid, "meta": m, "score": score})
        return out

    # ---------------- 维护 ----------------
    def clear(self, sid: str) -> int:
        """删除一个会话的全部数据，返回删除的键数量。"""
        n = self.r.delete(self._k_msg(sid), self._k_meta(sid))
        self.r.zrem(KEY_INDEX, sid)
        return n

    def stats(self) -> dict:
        """本次示例涉及的键统计：总数 + 按类型分组。"""
        by_type: dict[str, list[str]] = {}
        keys = []
        for k in self.r.scan_iter(match=KEY_PREFIX + "*", count=100):
            keys.append(k)
            t = self.r.type(k)
            by_type.setdefault(t, []).append(k)
        return {"total": len(keys), "by_type": by_type,
                "dbsize": self.r.dbsize(), "keys": sorted(keys)}


# --------------------------------------------------------------------------- #
# 展示辅助
# --------------------------------------------------------------------------- #
def print_history(msgs: list[dict], sid: str) -> None:
    if not msgs:
        print(f"  （会话 {sid} 暂无历史）")
        return
    print(f"\n  会话 {sid} 的历史（共 {len(msgs)} 条）：")
    for i, m in enumerate(msgs, 1):
        who = "用户  " if m.get("role") == "user" else "助手  "
        print(f"   {i:>2}. [{m.get('ts','')}] {who}| {m.get('content','')}")
    print()


def print_sessions(store: RedisChatHistory) -> None:
    rows = store.sessions()
    if not rows:
        print("  （还没有任何会话）")
        return
    print(f"\n  Redis ZSET `{KEY_INDEX}` 中的会话（按活跃时间倒序）：")
    print(f"  {'会话ID':<24}{'轮数':>6}   最后更新时间")
    print("  " + "-" * 62)
    for r in rows:
        m = r["meta"]
        print(f"  {r['session_id']:<24}{m.get('messages','0'):>6}   {m.get('updated_at','')}")
    print()


def print_keys(store: RedisChatHistory) -> None:
    st = store.stats()
    print(f"\n  Redis db{common.REDIS_DB} 共 {st['dbsize']} 个键，"
          f"其中本示例（前缀 `{KEY_PREFIX}`）产生 {st['total']} 个：")
    if not st["total"]:
        print("  （无）")
    for t, ks in sorted(st["by_type"].items()):
        print(f"  [{t}]")
        for k in ks:
            print(f"     {k}")
    print()


def print_raw(store: RedisChatHistory, sid: str) -> None:
    r = store.r
    k_msg, k_meta = store._k_msg(sid), store._k_meta(sid)
    print(f"\n  --- 底层结构：{k_msg}  (type={r.type(k_msg)}, len={r.llen(k_msg)}) ---")
    for i, item in enumerate(r.lrange(k_msg, 0, -1), 1):
        print(f"  {i:>2}: {item[:120]}")
    print(f"\n  --- 底层结构：{k_meta}  (type={r.type(k_meta)}) ---")
    for f, v in r.hgetall(k_meta).items():
        print(f"   {f:<12}= {v}")
    print(f"\n  --- 会话索引 ZSET {KEY_INDEX} ---")
    for sid_, score in r.zrevrange(KEY_INDEX, 0, -1, withscores=True):
        print(f"   {sid_:<24} score={score:.0f}")
    print()


# --------------------------------------------------------------------------- #
# 对话流程
# --------------------------------------------------------------------------- #
def one_turn(store: RedisChatHistory, sid: str, text: str) -> None:
    """处理一轮对话：写用户消息 -> 生成回复 -> 写回复。"""
    store.append(sid, "user", text)
    print(f"  用户  | {text}")

    reply = bot_reply(text, store.turns(sid))
    store.append(sid, "assistant", reply)
    print(f"  助手  | {reply}")


HELP = """可用命令：
  /history            查看当前会话历史
  /sessions           列出所有会话
  /switch <sid>       切换会话
  /new                新建会话
  /clear              清空当前会话
  /keys               查看 Redis 键
  /raw                查看底层 Redis 结构
  /help               显示帮助
  /quit               退出"""


def chat_loop(store: RedisChatHistory, sid: str) -> None:
    print("=" * 68)
    print("  Redis 会话历史演示 —— 输入内容开始对话（/help 看命令，/quit 退出）")
    print("=" * 68)
    print(f"  当前会话: {sid}")
    print(f"  历史条数: {len(store.history(sid))}\n")

    while True:
        try:
            line = input("你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见。")
            return
        if not line:
            continue

        if line.startswith("/"):
            cmd, _, arg = line.partition(" ")
            cmd = cmd.lower()
            if cmd in ("/quit", "/exit", "/q"):
                print("再见。")
                return
            elif cmd == "/history":
                print_history(store.history(sid), sid)
            elif cmd == "/sessions":
                print_sessions(store)
            elif cmd == "/switch":
                if not arg:
                    print("  用法：/switch <会话ID>")
                    continue
                sid = arg.strip()
                print(f"  已切换到会话 {sid}（历史 {len(store.history(sid))} 条）")
            elif cmd == "/new":
                sid = f"s-{uuid.uuid4().hex[:8]}"
                print(f"  已新建会话 {sid}")
            elif cmd == "/clear":
                n = store.clear(sid)
                print(f"  已清空会话 {sid}（删除 {n} 个键）")
            elif cmd == "/keys":
                print_keys(store)
            elif cmd == "/raw":
                print_raw(store, sid)
            elif cmd == "/help":
                print(HELP)
            else:
                print(f"  未知命令 {cmd}，输入 /help 查看帮助")
            continue

        one_turn(store, sid, line)


DEMO_SCRIPT = [
    "你好",
    "Redis 是怎么存对话历史的？",
    "那 Kafka 又是什么？",
    "我的历史记录还在吗？",
]


def run_demo(store: RedisChatHistory, sid: str) -> None:
    print("=" * 68)
    print("  Redis 会话历史演示（--demo 自动运行）")
    print("=" * 68)
    print(f"  会话 ID: {sid}\n")

    for text in DEMO_SCRIPT:
        one_turn(store, sid, text)

    print()
    print_history(store.history(sid), sid)

    # 演示多会话：再开一个会话，验证 ZSET 索引排序
    sid2 = f"s-{uuid.uuid4().hex[:8]}"
    print(f"  再创建一个会话 {sid2} 并写入一轮对话……")
    one_turn(store, sid2, "这是另一个会话的第一句话")
    print_sessions(store)

    print_raw(store, sid)
    print_keys(store)

    # 演示读取最近 N 条 —— 模拟"只把最近几轮送给大模型"
    recent = store.history(sid, limit=4)
    print(f"  最近 4 条（模拟拼 prompt 时只带最近上下文）：{len(recent)} 条")
    for m in recent:
        print(f"   - {m['role']:<9}| {m['content']}")
    print()

    print("  提示：这些数据已经持久化在 Redis 里，重新运行程序仍可 /history 查到。")
    print(f"  清理：python redis_chat_history.py --session {sid} --clear")


def inspect(store: RedisChatHistory) -> None:
    print("=" * 68)
    print("  Redis 中已存储的会话数据")
    print("=" * 68)
    print_sessions(store)
    print_keys(store)
    for s in store.sessions():
        print_history(store.history(s["session_id"]), s["session_id"])


# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(
        description="用 Redis 存储历史对话",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=HELP,
    )
    ap.add_argument("--session", default=None, help="会话 ID（默认自动生成）")
    ap.add_argument("--user", default="demo-user", help="用户标识")
    ap.add_argument("--demo", action="store_true", help="自动运行一段脚本化对话")
    ap.add_argument("--inspect", action="store_true", help="只查看 Redis 中已有数据")
    ap.add_argument("--clear", action="store_true", help="清空指定会话后退出")
    ap.add_argument("--ttl", type=int, default=TTL_SECONDS, help="会话过期秒数")
    args = ap.parse_args()

    sid = args.session or f"s-{uuid.uuid4().hex[:8]}"

    print(f"连接 Redis {common.REDIS_HOST}:{common.REDIS_PORT} "
          f"(db={common.REDIS_DB}, user={args.user}) ...")
    try:
        client = common.new_redis()
        client.ping()
    except Exception as e:                                   # noqa: BLE001
        print(f"\n[连接失败] {type(e).__name__}: {e}")
        print("请检查 common.py 里的 REDIS_HOST / REDIS_PORT / REDIS_PASSWORD，"
              "或设置同名环境变量后重试。")
        return 1
    print("连接成功。\n")

    store = RedisChatHistory(client, user=args.user, ttl=args.ttl)

    if args.inspect:
        inspect(store)
        return 0
    if args.clear:
        n = store.clear(sid)
        print(f"已清空会话 {sid}（删除 {n} 个键）")
        return 0
    if args.demo:
        run_demo(store, sid)
        return 0

    chat_loop(store, sid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
