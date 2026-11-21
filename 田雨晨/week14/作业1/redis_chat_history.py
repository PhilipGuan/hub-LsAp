import json
import time

import redis

r = redis.Redis(host="127.0.0.1", port=6379, decode_responses=True)
KEY = "chat:session:u1"


def append(role: str, text: str) -> None:
    r.rpush(KEY, json.dumps({"role": role, "text": text, "ts": int(time.time())}))
    r.expire(KEY, 3600)


def history() -> list[dict]:
    return [json.loads(x) for x in r.lrange(KEY, 0, -1)]


def demo() -> None:
    r.delete(KEY)
    append("user", "你好，帮我查一下订单")
    append("assistant", "好的，请提供订单号")
    append("user", "订单号 A12345")
    for i, m in enumerate(history(), 1):
        print(f"{i}. {m['role']:9s} {m['text']}")
    assert r.llen(KEY) == 3 and history()[-1]["role"] == "user"
    print(f"OK: {r.llen(KEY)} 条历史, TTL={r.ttl(KEY)}s")


if __name__ == "__main__":
    demo()
