# requirement install
pip install redis kafka-python

# 终端 1：启动消费者（先起可避免漏消息）
python kafka_consumer.py

# 终端 2：启动持久化消费者
python pipeline_consumer.py

# 终端 3：验证 Redis 单独功能
python redis_chat.py

# 终端 4：发消息
python kafka_producer.py

# 终端 5：验证 Kafka -> Redis 联动
python -c "import redis,json; r=redis.Redis(decode_responses=True); \
print([json.loads(x)['content'] for x in r.lrange('chat:user_1001',0,-1)])"


## 关键设计点说明
| 场景             | Redis 用法          | 说明                       |
|----------------|-------------------|--------------------------|
| 历史对话           | LIST + LTRIM      | 天然有序、支持范围读取，LTRIM 控制窗口大小 |
| 会话隔离| key 命名 chat:{sid} | 多用户互不影响                  |
| 自动清理	| EXPIRE            | 避免冷会话占内存                 |

| 场景	| Kafka 用法                             | 	说明                      |
|----------------|--------------------------------------|--------------------------|
| 分区顺序	| key=session_id	                      | 同一会话有序，消费端无需重排           |
| 多下游	| 不同 group_id	| analytics、persist 各自消费全量 |
| 可靠性| acks=all                             | 防丢消息                     |
| 读写分离| Kafka 写、Redis 读	                     | 高频读走 Redis，事件流走 Kafka    |


