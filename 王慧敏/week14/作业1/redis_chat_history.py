# -*- coding: utf-8 -*-
"""Redis 存储历史对话示例

依赖:
    pip install redis fakeredis

说明:
    默认用 fakeredis（纯 Python 内存版，无需安装/启动任何 Redis 服务），
    方便在 Windows 本地直接跑通示例。若要连接真实 Redis，见 get_client 里的注释。

设计要点:
    - 用 LIST 结构，key = chat:history:<session_id>，按会话隔离
    - LPUSH 新消息插到头部（最新在前）
    - LTRIM 只保留最近 max_len 条，防止历史无限增长
    - EXPIRE 设置过期时间，自动清理冷数据
    - pipeline 把多次写合并成一次往返，且保证原子性
"""

import json
import time

import redis
import fakeredis


def get_client(host="localhost", port=6379, db=0):
    """创建 Redis 客户端。

    默认使用 fakeredis（纯 Python 内存版，无需启动任何 Redis 服务），
    方便在 Windows 本地直接跑通示例。若要连接真实 Redis，把下面这行
    换成:
    """
    return redis.Redis(host=host, port=port, db=db, decode_responses=True)
    
    #return fakeredis.FakeRedis(decode_responses=True)


def save_message(client, session_id, role, content, max_len=50, ttl_seconds=7 * 24 * 3600):
    """保存一条消息到会话历史。

    :param client:      Redis 客户端
    :param session_id:  会话 ID（区分不同对话）
    :param role:        角色，如 "user" / "assistant"
    :param content:     消息正文
    :param max_len:     每个会话最多保留的条数
    :param ttl_seconds: 过期时间（秒），默认 7 天
    """
    key = f"chat:history:{session_id}"
    msg = json.dumps(
        {"role": role, "content": content, "ts": int(time.time() * 1000)},
        ensure_ascii=False,
    )

    pipe = client.pipeline()
    pipe.lpush(key, msg)                # 新消息插到头部（最新在前）
    pipe.ltrim(key, 0, max_len - 1)     # 只保留最近 max_len 条
    pipe.expire(key, ttl_seconds)       # 设置过期时间
    pipe.execute()                      # 原子执行

    
def get_history(client, session_id, limit=20):
    """取回最近 limit 条消息，按时间正序返回（方便直接拼给 LLM 上下文）。"""
    key = f"chat:history:{session_id}"
    items = client.lrange(key, 0, limit - 1)  # 最新在前
    return [json.loads(x) for x in reversed(items)]


def clear_history(client, session_id):
    """清空某个会话的历史。"""
    client.delete(f"chat:history:{session_id}")


if __name__ == "__main__":
    r = get_client()
    save_message(r, "sess_001", "user", "你好")
    save_message(r, "sess_001", "assistant", "你好！有什么可以帮你？")

    for m in get_history(r, "sess_001"):
        print(f"[{m['role']}] {m['content']}")
