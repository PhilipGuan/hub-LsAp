#!/usr/bin/env python3
"""使用 Redis List 保存和读取聊天历史。

数据结构：
    key   = chat:history:<session_id>
    value = Redis List，列表头部是最新消息
    item  = JSON 字符串，例如 {"role": "user", "content": "你好", ...}

程序使用 LPUSH + LTRIM + EXPIRE 保存有限数量的历史消息，
读取时用 LRANGE 取出，再反转成从旧到新的顺序。
"""

from __future__ import annotations

import argparse
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import redis


DEFAULT_REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
DEFAULT_TTL_SECONDS = int(os.getenv("CHAT_HISTORY_TTL_SECONDS", str(7 * 24 * 60 * 60)))
DEFAULT_MAX_MESSAGES = int(os.getenv("CHAT_HISTORY_MAX_MESSAGES", "50"))
VALID_ROLES = ("system", "user", "assistant", "tool")


class ChatHistoryStore:
    """把每个会话的聊天历史保存为一个 Redis List。"""

    def __init__(
        self,
        client: redis.Redis,
        *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        max_messages: int = DEFAULT_MAX_MESSAGES,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds 必须大于 0")
        if max_messages <= 0:
            raise ValueError("max_messages 必须大于 0")
        self.client = client
        self.ttl_seconds = ttl_seconds
        self.max_messages = max_messages

    @classmethod
    def from_url(
        cls,
        redis_url: str,
        *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        max_messages: int = DEFAULT_MAX_MESSAGES,
    ) -> "ChatHistoryStore":
        client = redis.Redis.from_url(redis_url, decode_responses=True)
        return cls(client, ttl_seconds=ttl_seconds, max_messages=max_messages)

    @staticmethod
    def key_for(session_id: str) -> str:
        session_id = session_id.strip()
        if not session_id:
            raise ValueError("session_id 不能为空")
        return f"chat:history:{session_id}"

    def add_message(self, session_id: str, role: str, content: str) -> dict[str, Any]:
        """写入一条消息，并只保留最近 max_messages 条。"""
        content = content.strip()
        if role not in VALID_ROLES:
            raise ValueError(f"role 必须是 {VALID_ROLES} 之一")
        if not content:
            raise ValueError("content 不能为空")

        message = {
            "id": uuid.uuid4().hex,
            "role": role,
            "content": content,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        key = self.key_for(session_id)
        payload = json.dumps(message, ensure_ascii=False, separators=(",", ":"))

        # pipeline 让三个 Redis 命令一次发送，避免历史记录处于半更新状态。
        with self.client.pipeline(transaction=True) as pipe:
            pipe.lpush(key, payload)
            pipe.ltrim(key, 0, self.max_messages - 1)
            pipe.expire(key, self.ttl_seconds)
            pipe.execute()
        return message

    def get_history(self, session_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        """按从旧到新的顺序读取聊天历史。"""
        key = self.key_for(session_id)
        actual_limit = self.max_messages if limit is None else min(max(limit, 1), self.max_messages)
        raw_messages = self.client.lrange(key, 0, actual_limit - 1)
        # LPUSH 后列表头最新，因此要反转成正常对话顺序。
        return [json.loads(item) for item in reversed(raw_messages)]

    def clear(self, session_id: str) -> int:
        """删除一个会话的全部历史，返回被删除的 key 数量。"""
        return int(self.client.delete(self.key_for(session_id)))


def print_history(history: list[dict[str, Any]]) -> None:
    if not history:
        print("(没有历史记录)")
        return
    for message in history:
        created_at = message.get("created_at", "")
        role = message.get("role", "unknown")
        content = message.get("content", "")
        print(f"[{created_at}] {role}: {content}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="用 Redis 保存聊天历史")
    parser.add_argument("--redis-url", default=DEFAULT_REDIS_URL, help="Redis 连接地址")
    parser.add_argument("--ttl", type=int, default=DEFAULT_TTL_SECONDS, help="历史记录过期秒数")
    parser.add_argument("--max-messages", type=int, default=DEFAULT_MAX_MESSAGES, help="每个会话最多保存多少条")

    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add", help="添加一条聊天消息")
    add_parser.add_argument("--session", default="demo", help="会话 ID，例如 user-001")
    add_parser.add_argument("--role", required=True, choices=VALID_ROLES, help="消息角色")
    add_parser.add_argument("--content", required=True, help="消息正文")

    list_parser = subparsers.add_parser("list", help="读取聊天历史")
    list_parser.add_argument("--session", default="demo", help="会话 ID")
    list_parser.add_argument("--limit", type=int, default=None, help="最多显示多少条")

    clear_parser = subparsers.add_parser("clear", help="清空一个会话的聊天历史")
    clear_parser.add_argument("--session", default="demo", help="会话 ID")

    demo_parser = subparsers.add_parser("demo", help="运行一组完整示例")
    demo_parser.add_argument("--session", default="demo", help="示例使用的会话 ID")

    return parser


def main() -> int:
    args = build_parser().parse_args()
    store = ChatHistoryStore.from_url(
        args.redis_url,
        ttl_seconds=args.ttl,
        max_messages=args.max_messages,
    )

    try:
        if args.command == "add":
            message = store.add_message(args.session, args.role, args.content)
            print(f"已写入消息 {message['id']}")

        elif args.command == "list":
            print(f"会话 {args.session} 的聊天历史：")
            print_history(store.get_history(args.session, limit=args.limit))

        elif args.command == "clear":
            deleted = store.clear(args.session)
            print(f"已清空会话 {args.session}，删除 key 数量：{deleted}")

        elif args.command == "demo":
            store.clear(args.session)
            examples = [
                ("user", "请解释一下 Redis 和 Kafka 的区别。"),
                ("assistant", "Redis 更偏向低延迟缓存与数据结构；Kafka 更偏向可持久化的消息流。"),
                ("user", "那聊天历史适合放在哪里？"),
                ("assistant", "最近的上下文可以放 Redis，长期记录和异步事件可以交给 Kafka 或数据库。"),
            ]
            for role, content in examples:
                store.add_message(args.session, role, content)

            key = store.key_for(args.session)
            print(f"Redis key: {key}")
            print(f"Redis 中保存的消息数量: {store.client.llen(key)}")
            print_history(store.get_history(args.session))
            print("提示：重复运行 demo 会先清空该示例会话，再写入示例消息。")

        return 0

    except redis.RedisError as exc:
        print(f"Redis 操作失败：{exc}")
        print("请先确认 Redis 已启动，或检查 REDIS_URL。")
        return 1
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"参数或数据错误：{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())