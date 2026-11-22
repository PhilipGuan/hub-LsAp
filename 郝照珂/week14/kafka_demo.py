"""作业 1 — 使用 produce 和 consume 两个子命令体验 Kafka 消息传递。"""
import argparse
import json

from kafka import KafkaConsumer

from app.broker import producer
from app.config import BROKERS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['produce', 'consume'])
    parser.add_argument('--topic', default='week14.messages')
    parser.add_argument('--message', default='你好，Kafka！')
    parser.add_argument('--group', default='week14-demo')
    args = parser.parse_args()
    if args.mode == 'produce':
        client = producer()
        try:
            result = client.send(args.topic, value={'message': args.message}).get(timeout=20)
            print(f'发送成功 topic={result.topic} partition={result.partition} offset={result.offset}')
        finally:
            client.close(timeout=5)
    else:
        client = KafkaConsumer(args.topic, bootstrap_servers=BROKERS, group_id=args.group,
                               auto_offset_reset='earliest', enable_auto_commit=False)
        try:
            for message in client:
                print(json.loads(message.value.decode('utf-8')), flush=True)
                client.commit()
        except KeyboardInterrupt:
            pass
        finally:
            client.close(timeout_ms=5000)


if __name__ == '__main__':
    main()
