"""作业 1 — Redis 持久化聊天历史，两个独立进程可读取同一会话。"""
import argparse
import json

from app.history import append_turn, client, read

DEFAULT_SESSION = '00000000-0000-4000-8000-000000000014'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', default=DEFAULT_SESSION)
    parser.add_argument('--question')
    parser.add_argument('--answer')
    args = parser.parse_args()
    client.ping()
    if bool(args.question) != bool(args.answer):
        parser.error('--question 与 --answer 需一起提供')
    if args.question:
        append_turn(args.session, args.question, args.answer)
    print(json.dumps(read(args.session), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
