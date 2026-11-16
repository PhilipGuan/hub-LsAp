# redis_chat.py
import json
import time
import redis

r = redis.Redis(host="localhost", port=6379, decode_responses=True)

SESSION_TTL = 3600          # 会话 1 小时过期
MAX_HISTORY = 100           # 单会话最多保留 100 条消息


def _key(session_id: str) -> str:
    return f"chat:{session_id}"


def add_message(session_id: str, role: str, content: str) -> None:
    """追加一条消息到会话历史"""
    msg = {"role": role, "content": content, "ts": time.time()}
    key = _key(session_id)

    pipe = r.pipeline()
    pipe.rpush(key, json.dumps(msg, ensure_ascii=False))
    pipe.ltrim(key, -MAX_HISTORY, -1)   # 只保留最新 MAX_HISTORY 条
    pipe.expire(key, SESSION_TTL)
    pipe.execute()


def get_history(session_id: str, last_n: int = 10) -> list[dict]:
    """获取最近 last_n 条消息（作为 LLM 上下文）"""
    raw = r.lrange(_key(session_id), -last_n, -1)
    return [json.loads(x) for x in raw]


def clear_session(session_id: str) -> None:
    r.delete(_key(session_id))


# ---------------- Demo ----------------
if __name__ == "__main__":
    sid = "user_1001"

    # 模拟一轮对话
    add_message(sid, "user", "你好，我叫小明")
    add_message(sid, "assistant", "你好小明，有什么可以帮你？")
    add_message(sid, "user", "帮我查下北京天气")
    add_message(sid, "assistant", "北京今天晴，25℃")

    print("=== 最近 3 条上下文 ===")
    for m in get_history(sid, last_n=3):
        print(f"[{m['role']}] {m['content']}")

    print(f"\n=== 当前会话总条数: {r.llen(_key(sid))} ===")
    print(f"TTL: {r.ttl(_key(sid))} 秒")

    # 演示多会话隔离
    add_message("user_1002", "user", "另一个用户的对话")
    print(f"\nuser_1002 历史: {get_history('user_1002')}")
    print(f"user_1001 历史条数: {len(get_history(sid, last_n=100))}")