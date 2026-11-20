# 第十五周作业 1：Redis 与 Kafka 实践

这个目录包含两个可运行程序：

| 文件 | 作用 |
| --- | --- |
| `redis_chat_history.py` | 用 Redis List 保存每个会话最近的聊天历史 |
| `kafka_message_demo.py` | 用 Kafka 生产者发送聊天消息，用消费者接收并打印消息 |
| `docker-compose.yml` | 一键启动本地 Redis 和 Kafka |
| `requirements.txt` | Python 依赖 |

## 一、作业 1：注册并提交 Company ID

这部分需要你本人完成注册，代码无法代替账号注册：

1. 打开 <https://console.compshare.cn/>。
2. 使用手机号或邮箱完成注册、登录和实名认证（如果平台要求）。
3. 在控制台的账号信息、个人中心或公司/组织信息页面找到 `Company ID`。
4. 按老师指定的提交方式，把 Company ID 发给老师。
5. 如果页面显示了敏感凭证，不要公开传播，只需要提交题目要求的 Company ID。

不同版本控制台的菜单名称可能略有差异，以你登录后页面显示为准。

## 二、整体思路

```text
用户消息
   |
   +--> Redis：保存某个 session 最近的若干条消息
   |
   +--> Kafka Producer：把消息作为事件发送到 topic
                         |
                         v
                  Kafka Consumer：异步处理、日志、风控、分析等
```

两种组件解决的问题不同：

- Redis 更像“高速短期记忆”。聊天窗口刷新后，可以用 `session_id` 快速取回最近上下文。
- Kafka 更像“可持久化的消息管道”。消息可以被一个或多个消费者异步处理，生产者和消费者不需要同时在线。

Redis 示例使用的数据结构：

```text
key   chat:history:user-001
type  List
内容  最新消息在左边
操作  LPUSH 写消息、LTRIM 只保留最近 N 条、EXPIRE 设置过期时间
```

Kafka 示例使用：

```text
topic          chat-messages
message key    sender
message value  JSON（message_id、sender、content、sent_at）
consumer group chat-demo-group
```

## 三、启动 Redis 和 Kafka

要求安装 Docker Desktop 或 Docker Engine，并且支持 `docker compose` 命令。

进入本目录：

```bash
cd Week15_作业1_Redis_Kafka
docker compose up -d
docker compose ps
```

第一次运行需要下载镜像，等待时间取决于网络。查看 Kafka 日志：

```bash
docker compose logs -f kafka
```

当 Kafka 正常启动后，按 `Ctrl+C` 退出日志查看（不会停止容器）。

如果没有 Docker，但老师已经提供了 Redis/Kafka 地址，可以跳过启动步骤，运行程序时设置：

```bash
export REDIS_URL='redis://你的地址:6379/0'
export KAFKA_BOOTSTRAP_SERVERS='你的地址:9092'
```

## 四、安装 Python 依赖

Linux / CompShare 镜像中：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -r requirements.txt
```

Windows PowerShell 中：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -r requirements.txt
```

## 五、程序 1：Redis 保存聊天历史

直接运行完整示例：

```bash
python redis_chat_history.py demo --session user-001
```

预期看到类似输出：

```text
Redis key: chat:history:user-001
Redis 中保存的消息数量: 4
[...] user: 请解释一下 Redis 和 Kafka 的区别。
[...] assistant: Redis 更偏向低延迟缓存与数据结构；Kafka 更偏向可持久化的消息流。
[...] user: 那聊天历史适合放在哪里？
[...] assistant: 最近的上下文可以放 Redis，长期记录和异步事件可以交给 Kafka 或数据库。
```

也可以逐条操作：

```bash
python redis_chat_history.py clear --session user-001
python redis_chat_history.py add --session user-001 --role user --content "你好"
python redis_chat_history.py add --session user-001 --role assistant --content "你好，有什么可以帮你？"
python redis_chat_history.py list --session user-001
```

控制保存数量和过期时间：

```bash
python redis_chat_history.py \
  --max-messages 20 \
  --ttl 86400 \
  demo --session user-001
```

其中：

- `--max-messages 20`：每个会话最多保留最近 20 条。
- `--ttl 86400`：一天没有写入后自动过期。
- `--session`：不同用户或不同聊天窗口应使用不同的 session ID。

可以直接用 Redis CLI 验证 List 确实存在：

```bash
docker compose exec redis redis-cli LLEN chat:history:user-001
docker compose exec redis redis-cli LRANGE chat:history:user-001 0 -1
```

## 六、程序 2：Kafka 传递消息

Kafka 需要先有消费者，或者至少保证消息已经被写入 topic。推荐开两个终端。

终端 A，启动消费者：

```bash
source .venv/bin/activate
python kafka_message_demo.py consume \
  --group homework-001 \
  --max-messages 2 \
  --idle-timeout 30
```

终端 B，发送消息：

```bash
source .venv/bin/activate
python kafka_message_demo.py send --sender alice --content "你好，这是一条 Kafka 消息"
python kafka_message_demo.py send --sender bob --content "收到，消费者正在处理"
```

终端 A 会显示类似内容：

```text
收到消息：partition=0, offset=0, sender=alice, content='你好，这是一条 Kafka 消息', ...
收到消息：partition=0, offset=1, sender=bob, content='收到，消费者正在处理', ...
消费结束，共处理 2 条消息。
```

如果消费者已经停止，仍然可以先把消息发到 Kafka，再用一个**新的消费者组**读取：

```bash
python kafka_message_demo.py send --sender alice --content "离线消息"
python kafka_message_demo.py consume --group homework-002 --max-messages 1
```

这里使用新的 `group` 很重要。Kafka 会为每个消费者组记录 offset；同一组已经消费过的消息，默认不会再次从头读取。

查看 topic 是否创建成功：

```bash
docker compose exec kafka kafka-topics \
  --bootstrap-server localhost:9092 --list
```

## 七、代码中的关键点

### Redis

```python
pipe.lpush(key, payload)              # 新消息放到列表头
pipe.ltrim(key, 0, max_messages - 1)  # 只保留最近 N 条
pipe.expire(key, ttl_seconds)         # 设置过期时间
```

读取时：

```python
raw_messages = client.lrange(key, 0, limit - 1)
history = [json.loads(item) for item in reversed(raw_messages)]
```

因为写入时用了 `LPUSH`，列表是“新到旧”，所以读取后要反转成“旧到新”。

### Kafka

生产者发送 JSON：

```python
producer.send(topic, key=sender, value=message)
```

同一个 `key` 会优先进入同一个分区，因此同一个发送者的消息通常保持顺序。

消费者关闭自动提交，处理完再提交：

```python
enable_auto_commit=False
consumer.commit()
```

这体现了 Kafka 常用的“至少一次”处理思路：如果程序在处理后、提交前崩溃，消息可能会再次被消费，所以真实业务需要幂等处理。

## 八、常见问题

### 1. Kafka 启动后立刻连接失败

Kafka 启动需要一些时间。先等待 20～30 秒，再运行：

```bash
docker compose ps
docker compose logs --tail=100 kafka
```

### 2. 本机能连，容器里连不上

本示例把 `KAFKA_ADVERTISED_LISTENERS` 设置成 `localhost:9092`，适用于“代码运行在宿主机，Kafka 运行在 Docker”的情况。

如果以后把 Python 程序也放进 Docker Compose，broker 地址应改成服务名 `kafka:9092`，并相应配置监听器。

### 3. 消费者读不到旧消息

原因通常是消费者组已经提交过 offset。换一个新的组：

```bash
python kafka_message_demo.py consume --group homework-$(date +%s) --max-messages 1
```

### 4. 镜像下载慢

先确认镜像源可用，再执行 `docker compose pull`。不要为了演示把 Redis/Kafka 端口直接暴露到公网。

### 5. 如何清理环境

停止容器，但保留数据：

```bash
docker compose down
```

连数据一起删除：

```bash
docker compose down -v
```

`-v` 会删除 Redis 和 Kafka 的持久化数据，只在确认不需要旧数据时使用。

## 九、提交作业时可以写什么

可以按下面的结构整理一份简短说明：

1. 注册 CompShare 后提交的 Company ID。
2. Redis 示例截图：`demo` 输出以及 `redis-cli` 中的 List。
3. Kafka 示例截图：一个终端消费、一个终端发送，并显示 offset。
4. 说明 Redis 用于保存最近聊天上下文，Kafka 用于异步传递消息流。
5. 说明 Redis 的 `LPUSH + LTRIM + EXPIRE` 和 Kafka 的 `topic + producer + consumer group` 各自的作用。