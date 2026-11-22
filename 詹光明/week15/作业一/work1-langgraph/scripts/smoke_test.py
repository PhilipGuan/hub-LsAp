"""命令行冒烟测试：不启动前端也能验证「情感识别 → 鼓励 / 排解」整条链路。

用法（在 work1-langgraph 目录下）：

    # 先启动后端，再运行默认演示场景
    python scripts/smoke_test.py

    # 不经过 HTTP，直接在本进程运行 LangGraph
    python scripts/smoke_test.py --direct

    # 自定义一句话（可重复 -m 形成多轮）
    python scripts/smoke_test.py -m "今天好开心" -m "想和你多聊聊"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Windows 下输出被重定向时默认不是 UTF-8，中文会变乱码。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover - 非标准 stdout 时忽略
    pass

DEFAULT_BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8000")

# 三条单轮场景，分别覆盖鼓励 / 排解 / 中性回应。
SINGLE_TURN_SCENARIOS = [
    ("正向", "今天项目终于上线了，同事们配合得特别默契，我现在超开心！"),
    ("负面", "最近压力好大，晚上总是睡不着，感觉自己什么都做不好。"),
    ("中性", "我明天要坐高铁去杭州出差，下午三点出发。"),
]

# 同一个 thread_id 下的两轮对话，验证多轮记忆。
MULTI_TURN_MESSAGES = [
    "今天被领导当众批评了，心里特别难受。",
    "我现在一点都不想说话，只想一个人待着。",
]


def print_turn(index: int, message: str, data: dict) -> None:
    emotion = data.get("emotion") or {}
    reply = data.get("reply") or {}
    confidence = float(emotion.get("confidence", 0.0) or 0.0)

    print(f"\n[{index}] 用户：{message}")
    print(
        "    情感："
        f"{emotion.get('label', '?')} / {emotion.get('emotion', '?')} / "
        f"置信度 {confidence:.2f} / 强度 {emotion.get('intensity', '?')}/5"
    )
    print(f"    依据：{emotion.get('reason', '?')}")
    if emotion.get("risk"):
        print("    ⚠️ 危机信号：是")
    print(f"    策略：{reply.get('strategy', '?')}")
    print(f"    回复：{reply.get('reply_text', '?')}")
    for suggestion in reply.get("suggestions") or []:
        print(f"      - {suggestion}")
    print(f"    轨迹：{' -> '.join(data.get('trace') or [])}")


def run_http(
    backend_url: str, messages: list[str], thread_id: str | None
) -> list[dict]:
    results: list[dict] = []
    for message in messages:
        payload: dict = {"message": message}
        if thread_id:
            payload["thread_id"] = thread_id
        response = httpx.post(
            f"{backend_url.rstrip('/')}/api/chat", json=payload, timeout=120.0
        )
        response.raise_for_status()
        data = response.json()
        thread_id = data["thread_id"]
        results.append(data)
    return results


async def _run_direct_async(
    messages: list[str], thread_id: str | None
) -> list[dict]:
    from backend.service import ChatService

    service = ChatService()
    results: list[dict] = []
    for message in messages:
        response = await service.chat(message, thread_id)
        thread_id = response.thread_id
        results.append(response.model_dump())
    return results


def run_direct(
    messages: list[str], thread_id: str | None
) -> list[dict]:
    return asyncio.run(_run_direct_async(messages, thread_id))


def check_health(backend_url: str) -> None:
    try:
        response = httpx.get(
            f"{backend_url.rstrip('/')}/health", timeout=5.0
        )
        response.raise_for_status()
        data = response.json()
    except Exception as exc:  # noqa: BLE001
        print(f"❌ 无法访问后端 {backend_url}/health：{exc}")
        print("   请先运行：uvicorn backend.main:app --host 127.0.0.1 --port 8000")
        raise SystemExit(1) from exc

    print(f"✅ 后端已连接：{backend_url}")
    print(f"   模型：{data.get('model')}")
    print(f"   API key 已配置：{data.get('api_key_configured')}")
    if not data.get("api_key_configured"):
        print("   ⚠️ 未配置 API key，回复会降级为兜底文案。")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="情绪陪伴助手冒烟测试")
    parser.add_argument(
        "--backend-url",
        default=DEFAULT_BACKEND_URL,
        help=f"后端地址，默认 {DEFAULT_BACKEND_URL}",
    )
    parser.add_argument(
        "--direct",
        action="store_true",
        help="不经过 HTTP，直接在本进程运行 LangGraph",
    )
    parser.add_argument(
        "-m",
        "--message",
        action="append",
        default=[],
        help="自定义用户消息，可重复传入形成多轮",
    )
    parser.add_argument(
        "--thread-id",
        default=None,
        help="指定会话 ID；不传则自动生成",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="额外打印完整响应 JSON",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.message:
        runs = [(args.message, args.thread_id or uuid.uuid4().hex)]
    else:
        runs = [
            ([message], uuid.uuid4().hex)
            for _, message in SINGLE_TURN_SCENARIOS
        ]
        runs.append((MULTI_TURN_MESSAGES, uuid.uuid4().hex))

    if args.direct:
        from backend.config import get_settings

        settings = get_settings()
        print(f"直接运行模式：{settings.model} @ {settings.base_url}")
        if not settings.api_key_configured:
            print("⚠️ 未检测到 OPENAI_API_KEY，回复会降级为兜底文案。")
        runner = lambda messages, thread_id: run_direct(messages, thread_id)  # noqa: E731
    else:
        check_health(args.backend_url)
        runner = lambda messages, thread_id: run_http(  # noqa: E731
            args.backend_url, messages, thread_id
        )

    turn = 0
    failures = 0
    for messages, thread_id in runs:
        try:
            results = runner(messages, thread_id)
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"\n❌ 场景失败（thread={thread_id}）：{exc}")
            continue

        for message, data in zip(messages, results):
            turn += 1
            print_turn(turn, message, data)
            if args.json:
                print(json.dumps(data, ensure_ascii=False, indent=2))

    print(f"\n完成：共 {turn} 轮，失败 {failures} 个场景。")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
