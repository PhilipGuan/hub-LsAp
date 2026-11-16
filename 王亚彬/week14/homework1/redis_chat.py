# 用 Redis 存储历史对话：list 结构 + 过期时间
import os
import redis

r = redis.Redis(
    host=os.getenv("REDIS_HOST", "localhost"),
    port=6379,
    decode_responses=True,
)

key = "chat:1001"
r.delete(key)  # 清掉上次运行的残留

messages = [
    "user: 你好，什么是 RAG？",
    "assistant: RAG 是检索增强生成，先从知识库检索资料，再让模型基于资料回答。",
    "user: 它和微调有什么区别？",
    "assistant: 微调改模型参数，RAG 不改模型，只替换外部知识。",
]
r.rpush(key, *messages)
r.expire(key, 3600)  # 1 小时后自动过期

# 新的一轮对话继续往同一个 key 追加
r.rpush(key, "user: 明白了，谢谢")

print(f"会话 {key} 共 {r.llen(key)} 条消息：")
for i, m in enumerate(r.lrange(key, 0, -1), 1):
    print(f"  {i}. {m}")
print("剩余存活时间(秒):", r.ttl(key))
