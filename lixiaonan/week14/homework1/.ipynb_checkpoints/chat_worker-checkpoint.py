#!/bin/python

import json
import redis
from kafka import KafkaConsumer

# 1. 连接 Redis
# decode_responses=True 让 redis 返回字符串而不是 bytes
r = redis.Redis(host='127.0.0.1', port=6379, db=0, decode_responses=True)

# 2. 连接 Kafka 消费者
consumer = KafkaConsumer(
    'chat-topic',                   # 订阅的主题
    bootstrap_servers='127.0.0.1:9092', 
    group_id='chat-bot-group',      # 消费者组
    auto_offset_reset='latest',     # 从最新消息开始读
    value_deserializer=lambda m: json.loads(m.decode('utf-8'))
)

print("🤖 聊天机器人已启动，等待接收消息...")

def process_message(user_id, message):
    """处理消息的核心逻辑：读写 Redis"""
    redis_key = f"chat_history:{user_id}"
    
    # 1. 从 Redis 获取该用户的历史对话（取最近 10 条）
    history = r.lrange(redis_key, 0, 9)
    history.reverse() # Redis lrange 是倒序的，反转一下让顺序正常
    
    print(f"\n--- 收到用户 [{user_id}] 的消息: {message} ---")
    print(f"📚 从 Redis 读取到的历史对话: {history}")
    
    # 2. 模拟 AI 生成回复（这里简单拼接，实际可接入大模型）
    reply = f"收到你的消息：'{message}'。这是你第 {len(history) + 1} 次和我聊天。"
    print(f"💬 机器人回复: {reply}")
    
    # 3. 将用户消息和机器人回复存回 Redis
    # lpush 将新消息推入列表头部
    r.lpush(redis_key, f"User: {message}")
    r.lpush(redis_key, f"Bot: {reply}")
    # 裁剪列表，只保留最近 20 条，防止内存无限增长
    r.ltrim(redis_key, 0, 19)

# 3. 循环消费 Kafka 消息
try:
    for msg in consumer:
        data = msg.value
        user_id = data.get("user_id", "default_user")
        message = data.get("message", "")
        
        if message:
            process_message(user_id, message)
except KeyboardInterrupt:
    print("\n🛑 机器人已停止。")