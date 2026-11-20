# redis_chat_history.py
import redis
import json
from datetime import datetime

# 连接 Redis
r = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)

class ChatHistory:
    def __init__(self, session_id: str, max_len: int = 100):
        self.key = f"chat:history:{session_id}"
        self.max_len = max_len

    def add_message(self, role: str, content: str):
        """添加一条消息，并裁剪到最大长度"""
        msg = {
            "role": role,
            "content": content,
            "time": datetime.now().isoformat()
        }
        # 使用 Redis List 追加到右侧
        r.rpush(self.key, json.dumps(msg))
        # 只保留最近 max_len 条
        r.ltrim(self.key, -self.max_len, -1)

    def get_recent(self, count: int = 10):
        """获取最近 count 条消息，按时间顺序返回"""
        items = r.lrange(self.key, -count, -1)
        return [json.loads(i) for i in items]

    def clear(self):
        r.delete(self.key)


# 使用示例
if __name__ == "__main__":
    chat = ChatHistory("user_001")

    # 模拟写入对话
    chat.add_message("user", "你好，请帮我写一个 Python 函数")
    chat.add_message("assistant", "好的，请告诉我函数要实现什么功能？")
    chat.add_message("user", "计算两个数的和")
    chat.add_message("assistant", "def add(a, b):\n    return a + b")

    # 读取最近对话
    print("=== 最近对话 ===")
    for msg in chat.get_recent(5):
        print(f"[{msg['time']}] {msg['role']}: {msg['content']}")

    # 演示多会话隔离
    chat2 = ChatHistory("user_002")
    chat2.add_message("user", "另一个用户的对话")
    print("\n=== 用户2的对话 ===")
    print(chat2.get_recent(3))