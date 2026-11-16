"""
程序二：用 Kafka 传递消息（生产者 / 消费者）

架构
--------------------------------------------------------------------
  ┌──────────────┐   key=session_id   ┌───────────────┐   ┌──────────────┐
  │  Producer    │ ─────────────────► │ Kafka Broker  │─► │  Consumer    │
  │ 模拟对话输入 │   value=JSON 事件  │ chat-events   │   │ 处理/落库     │
  └──────────────┘                    └───────────────┘   └──────────────┘

关键点
--------------------------------------------------------------------
  * key = session_id：同一会话的消息落到同一分区，保证会话内顺序
  * acks='all' + retries：发送可靠性
  * 消费端手动提交 offset（enable_auto_commit=False），失败可重放
  * 消费端 --sink redis 可把消息写入 Redis 历史（与程序一联动）

运行方式
--------------------------------------------------------------------
  python kafka_messaging.py topics                 # 查看/创建 topic
  python kafka_messaging.py produce                # 交互式发消息
  python kafka_messaging.py consume                # 消费消息
  python kafka_messaging.py consume --sink redis   # 消费并写入 Redis 历史
  python kafka_messaging.py demo                   # 一键完整演示（发 + 收）
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid

import common


# --------------------------------------------------------------------------- #
# 事件构造
# --------------------------------------------------------------------------- #
def make_event(session_id: str, role: str, content: str, seq: int) -> dict:
    """一条对话事件。seq 用来在消费端验证分区内顺序。"""
    return {
        "event": "chat_message",
        "session_id": session_id,
        "seq": seq,
        "role": role,
        "content": content,
        "ts": common.now_ts(),
    }


# --------------------------------------------------------------------------- #
# 序列化/反序列化（显式类型标注，避免 object 访问 encode/decode 的静态检查报错）
# --------------------------------------------------------------------------- #
def _key_ser(key: str) -> bytes:
    return key.encode("utf-8")


def _value_ser(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def _key_de(raw: bytes | None) -> str | None:
    return raw.decode("utf-8") if raw else None


def _value_de(raw: bytes) -> dict:
    return json.loads(raw.decode("utf-8"))


# --------------------------------------------------------------------------- #
# 生产者
# --------------------------------------------------------------------------- #
def build_producer():
    from kafka import KafkaProducer

    return KafkaProducer(
        bootstrap_servers=common.KAFKA_BOOTSTRAP,
        acks="all",                 # 等所有 ISR 确认，最可靠
        retries=3,
        linger_ms=10,               # 小幅攒批，提高吞吐
        max_in_flight_requests_per_connection=1,   # 配合 acks=all 保证顺序
        key_serializer=_key_ser,
        value_serializer=_value_ser,
    )


def send(producer, session_id: str, role: str, content: str, seq: int,
         verbose: bool = True) -> None:
    """发送一条事件，key 用 session_id 保证同会话进入同一分区。"""
    event = make_event(session_id, role, content, seq)

    def _on_send(meta):
        if verbose:
            print(f"  ✓ 已发送 {meta.topic}[p{meta.partition}] offset={meta.offset} "
                  f"| session={session_id} seq={seq} role={role}")

    producer.send(common.KAFKA_TOPIC, key=session_id, value=event).add_callback(_on_send)
    producer.flush()      # 演示用：阻塞等待确认，确保输出顺序清晰


# --------------------------------------------------------------------------- #
# 消费者
# --------------------------------------------------------------------------- #
def build_consumer(group_id: str, earliest: bool = True, timeout_ms: int | None = None):
    from kafka import KafkaConsumer

    extra = {} if timeout_ms is None else {"consumer_timeout_ms": timeout_ms}
    return KafkaConsumer(
        common.KAFKA_TOPIC,
        bootstrap_servers=common.KAFKA_BOOTSTRAP,
        group_id=group_id,
        auto_offset_reset="earliest" if earliest else "latest",
        enable_auto_commit=False,        # 手动提交，处理完再确认
        key_deserializer=_key_de,
        value_deserializer=_value_de,
        **extra,
    )


class RedisSink:
    """可选：把消费到的消息写进 Redis 会话历史（复用程序一的存储层）。"""

    def __init__(self):
        from redis_chat_history import RedisChatHistory

        self.client = common.new_redis()
        self.client.ping()
        self.store = RedisChatHistory(self.client, user="kafka-consumer")

    def write(self, event: dict) -> None:
        self.store.append(
            event["session_id"],
            event.get("role", "user"),
            event.get("content", ""),
        )


def consume(group_id: str, limit: int = 0, timeout_ms: int | None = 10000,
            sink: RedisSink | None = None) -> int:
    """消费消息。limit=0 表示不限条数（直到超时）。"""
    consumer = build_consumer(group_id, earliest=(limit > 0 or timeout_ms is not None),
                              timeout_ms=timeout_ms)
    print(f"消费中: topic={common.KAFKA_TOPIC} group={group_id} "
          f"bootstrap={common.KAFKA_BOOTSTRAP}")
    print("-" * 68)

    n = 0
    last_session = None
    try:
        for record in consumer:
            ev = record.value
            sid = record.key or ev.get("session_id")
            mark = "" if sid == last_session else "  ← 新会话"
            last_session = sid
            print(f"  [p{record.partition} @{record.offset}] session={sid} "
                  f"seq={ev.get('seq')} {ev.get('role')}: {ev.get('content')}{mark}")

            if sink is not None:
                sink.write(ev)

            n += 1
            # 处理成功后再提交 offset（手动提交）
            consumer.commit()
            if limit and n >= limit:
                break
    except KeyboardInterrupt:
        print("\n  收到中断，停止消费。")
    finally:
        consumer.close()

    print("-" * 68)
    print(f"共消费 {n} 条消息。")
    if sink is not None:
        print("已同步写入 Redis 会话历史，可用 redis_chat_history.py --inspect 查看。")
    return n


# --------------------------------------------------------------------------- #
# topic 管理
# --------------------------------------------------------------------------- #
def describe_topics(create: bool = False, partitions: int = 3) -> None:
    from kafka import KafkaAdminClient

    admin = KafkaAdminClient(bootstrap_servers=common.KAFKA_BOOTSTRAP,
                            client_id="demo-admin")
    existing = set(admin.list_topics())
    print(f"集群 {common.KAFKA_BOOTSTRAP} 上的 topic（{len(existing)} 个）：")
    for t in sorted(existing):
        print(f"  - {t}")

    if create and common.KAFKA_TOPIC not in existing:
        from kafka.admin import NewTopic

        admin.create_topics([NewTopic(name=common.KAFKA_TOPIC,
                                      num_partitions=partitions,
                                      replication_factor=1)])
        print(f"\n已创建 topic `{common.KAFKA_TOPIC}`"
              f"（partitions={partitions}, replication=1）")
    elif create:
        print(f"\ntopic `{common.KAFKA_TOPIC}` 已存在，无需创建。")

    # 打印目标 topic 的分区分布
    from kafka import KafkaConsumer as _KC

    c = _KC(bootstrap_servers=common.KAFKA_BOOTSTRAP)
    parts = c.partitions_for_topic(common.KAFKA_TOPIC)
    if parts:
        print(f"\ntopic `{common.KAFKA_TOPIC}` 的分区：{sorted(parts)}")
    else:
        print(f"\ntopic `{common.KAFKA_TOPIC}` 还不存在（首次发送时会自动创建）。")
    c.close()
    admin.close()


# --------------------------------------------------------------------------- #
# 各模式
# --------------------------------------------------------------------------- #
def cmd_produce(session_id: str) -> int:
    producer = build_producer()
    print(f"生产者就绪: {common.KAFKA_BOOTSTRAP} -> topic `{common.KAFKA_TOPIC}`")
    print(f"会话: {session_id}（输入内容发送，空行退出）\n")

    seq = 0
    try:
        while True:
            try:
                line = input("发 > ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
                break
            send(producer, session_id, "user", line, seq)
            seq += 1
            reply = common.bot_reply(line, seq)
            send(producer, session_id, "assistant", reply, seq)
            seq += 1
            print(f"  助手  | {reply}")
    finally:
        producer.close()
    print(f"\n共发送 {seq} 条消息。用 `python kafka_messaging.py consume` 消费。")
    return 0


DEMO_LINES = [
    "你好，我是通过 Kafka 发来的第一条消息",
    "Redis 存历史，Kafka 传消息",
    "这条消息用 session_id 作为 key，会落到固定分区",
]


def cmd_demo(session_a: str, session_b: str) -> int:
    print("=" * 68)
    print("  Kafka 消息传递演示：2 个会话并发发送 -> 消费验证顺序")
    print("=" * 68)
    print(f"  topic={common.KAFKA_TOPIC}  bootstrap={common.KAFKA_BOOTSTRAP}\n")

    describe_topics(create=True, partitions=3)
    print()

    producer = build_producer()
    print("【1】生产者发送消息")
    seq_a = seq_b = 0
    for i, line in enumerate(DEMO_LINES):
        send(producer, session_a, "user", line, seq_a)
        seq_a += 1
        reply = common.bot_reply(line, i)
        send(producer, session_a, "assistant", reply, seq_a)
        seq_a += 1

        # 另一个会话穿插发送，验证按 key 分区后各自有序
        send(producer, session_b, "user", f"会话B的第{i + 1}条", seq_b)
        seq_b += 1
    producer.close()
    print(f"\n  共发送 {seq_a + seq_b} 条（会话A {seq_a} 条，会话B {seq_b} 条）\n")

    print("【2】消费者消费（新消费组，从头读）")
    group = f"demo-group-{uuid.uuid4().hex[:6]}"
    consume(group, limit=seq_a + seq_b, timeout_ms=8000)

    print("\n【3】同一个消费组再消费一次（offset 已提交，应读到 0 条新消息）")
    consume(group, limit=0, timeout_ms=4000)

    print("\n【4】换一个消费组带上 Redis 落库（Kafka -> Redis 联动）")
    try:
        sink = RedisSink()
        print("  Redis 连接成功，开始消费并写入历史……")
        consume(f"redis-sink-{uuid.uuid4().hex[:6]}", limit=seq_a + seq_b,
                timeout_ms=8000, sink=sink)
    except Exception as e:                                   # noqa: BLE001
        print(f"  [跳过] Redis 不可用：{type(e).__name__}: {e}")

    print(f"\n演示结束。会话A={session_a} 会话B={session_b}")
    print("验证：python redis_chat_history.py --inspect")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="用 Kafka 传递消息")
    sub = ap.add_subparsers(dest="mode")

    p_topics = sub.add_parser("topics", help="查看/创建 topic")
    p_topics.add_argument("--create", action="store_true", help="不存在则创建")
    p_topics.add_argument("--partitions", type=int, default=3)

    p_prod = sub.add_parser("produce", help="交互式发送消息")
    p_prod.add_argument("--session", default=None)

    p_cons = sub.add_parser("consume", help="消费消息")
    p_cons.add_argument("--group", default=common.KAFKA_GROUP)
    p_cons.add_argument("--limit", type=int, default=0, help="最多消费 N 条（0=不限）")
    p_cons.add_argument("--timeout", type=int, default=10000, help="空闲超时毫秒")
    p_cons.add_argument("--sink", choices=["redis"], default=None,
                        help="把消息写入 Redis 历史")

    sub.add_parser("demo", help="一键完整演示")

    args = ap.parse_args()
    mode = args.mode or "demo"

    try:
        if mode == "topics":
            describe_topics(create=args.create, partitions=args.partitions)
            return 0
        if mode == "produce":
            return cmd_produce(args.session or f"s-{uuid.uuid4().hex[:8]}")
        if mode == "consume":
            sink = RedisSink() if args.sink == "redis" else None
            consume(args.group, limit=args.limit,
                    timeout_ms=args.timeout, sink=sink)
            return 0
        return cmd_demo(f"s-{uuid.uuid4().hex[:8]}", f"s-{uuid.uuid4().hex[:8]}")
    except Exception as e:                                   # noqa: BLE001
        print(f"\n[运行失败] {type(e).__name__}: {e}")
        print(f"请检查 Kafka 是否可达：{common.KAFKA_BOOTSTRAP}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
