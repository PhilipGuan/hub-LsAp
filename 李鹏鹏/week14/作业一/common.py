"""
两个示例程序共用的配置与工具函数。

所有连接参数都可以用环境变量覆盖，方便在不同环境/机器上直接跑：
    REDIS_HOST / REDIS_PORT / REDIS_PASSWORD / REDIS_DB
    KAFKA_BOOTSTRAP / KAFKA_TOPIC / KAFKA_GROUP
"""
from __future__ import annotations

import os
import sys
from datetime import datetime


def setup_console() -> None:
    """Windows 控制台默认 GBK，强制改用 UTF-8 输出，避免中文乱码。"""
    for stream in (sys.stdout, sys.stderr):
        enc = (getattr(stream, "encoding", "") or "").lower()
        if enc not in ("utf-8", "utf8"):
            try:
                stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
            except (AttributeError, ValueError):
                pass


setup_console()

# --------------------------------------------------------------------------- #
# Redis 连接配置
# --------------------------------------------------------------------------- #
REDIS_HOST = os.getenv("REDIS_HOST", "172.30.0.4")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "Redis@456")
REDIS_DB = int(os.getenv("REDIS_DB", "0"))

# --------------------------------------------------------------------------- #
# Kafka 连接配置
# --------------------------------------------------------------------------- #
# 注意：172.30.0.4:9092 端口上的 broker 通告(advertised.listeners)的是 localhost:9092，
# 外部客户端会去连自己机器的 9092 从而超时；9094 端口通告的是 172.30.0.4:9094，可正常使用。
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "172.30.0.4:9094")
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "chat-events")
KAFKA_GROUP = os.getenv("KAFKA_GROUP", "chat-history-writer")


def new_redis(decode_responses: bool = True):
    """创建并返回一个 Redis 客户端（连接池由 SDK 内部管理）。"""
    import redis

    return redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        password=REDIS_PASSWORD,
        db=REDIS_DB,
        decode_responses=decode_responses,
        socket_connect_timeout=5,
        socket_timeout=5,
        health_check_interval=30,
    )


def now_ts() -> str:
    """可读时间戳，写进消息体里便于排查。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def bot_reply(user_text: str, turns: int = 0) -> str:
    """
    一个离线的"假 LLM"：不依赖任何外部 API，保证示例可以直接运行。
    真实项目里把这里换成 openai / 本地大模型的调用即可。
    """
    text = (user_text or "").strip()
    if not text:
        return "我没听清，可以再说一次吗？"

    low = text.lower()

    if any(k in low for k in ("你好", "您好", "hello", "hi")):
        return "你好！我是这个示例里的演示助手，我的对话历史存在 Redis 里。"
    if "redis" in low:
        return (
            "Redis 是内存型键值数据库。这里我用 LIST 存对话流水、"
            "HASH 存会话元信息、ZSET 做会话活跃度排行榜。"
        )
    if "kafka" in low:
        return (
            "Kafka 是分布式消息队列：生产者按 key 写入分区，消费者组按 offset 消费，"
            "支持多消费者水平扩展。"
        )
    if any(k in low for k in ("历史", "history", "记不住", "忘了")):
        return "可以用 /history 查看当前会话历史；换会话用 /switch <会话ID>。"
    if text.endswith(("?", "？")):
        return f"（演示回复）关于「{text.rstrip('?？')}」，这只是个离线示例，还没有接入真实模型。"
    return f"收到，这是第 {turns + 1} 轮对话。你刚才说的是：{text}"
