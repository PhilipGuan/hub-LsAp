"""四个进程共用的运行骨架。

- 前端进程：frontend_main
- 后端进程：backend_main -> run_backend -> orchestrator
- 进程1/进程2：serve(handler, 请求队列, 响应队列)
"""
from __future__ import annotations

import sys
import traceback
from queue import Empty
from typing import Any, Callable

from .protocol import ErrorResult, ReplyResult, UserRequest, new_rid

Handler = Callable[[Any], Any]

_EXIT_WORDS = {"exit", "quit", "q", "bye", "退出", "再见"}


def log(message: str) -> None:
    """统一日志：写 stderr，不和前端对话的 stdout 混在一起。"""
    print(f"[pipeline] {message}", file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- #
# 进程1 / 进程2 的主循环
# --------------------------------------------------------------------------- #
def serve(handler: Handler, request_queue: Any, response_queue: Any) -> None:
    """收请求 -> 调用 handler -> 回结果；收到 None 时退出。"""
    while True:
        try:
            request = request_queue.get()
        except (EOFError, OSError):
            return
        if request is None:
            return
        try:
            response_queue.put(handler(request))
        except Exception:
            response_queue.put(
                ErrorResult(
                    rid=getattr(request, "rid", "-"),
                    error=traceback.format_exc(limit=5),
                )
            )


# --------------------------------------------------------------------------- #
# 后端进程里的同步 RPC
# --------------------------------------------------------------------------- #
def call_stage(
    request_queue: Any,
    response_queue: Any,
    request: Any,
    expected_type: type,
    timeout: float = 180.0,
) -> Any:
    """把请求丢给某个进程并阻塞等结果，出错时抛异常。"""
    request_queue.put(request)
    try:
        response = response_queue.get(timeout=timeout)
    except Empty:
        raise TimeoutError(f"等待 {expected_type.__name__} 超时（{timeout:.0f}s）") from None

    if isinstance(response, ErrorResult):
        raise RuntimeError(f"{expected_type.__name__} 处理失败：\n{response.error}")
    if not isinstance(response, expected_type):
        raise TypeError(
            f"期望 {expected_type.__name__}，实际收到 {type(response).__name__}"
        )
    return response


# --------------------------------------------------------------------------- #
# 后端进程
# --------------------------------------------------------------------------- #
def run_backend(
    front_in: Any,
    front_out: Any,
    orchestrator: Callable[[str, str], str],
) -> None:
    """串行处理前端请求：orchestrator(text, session_id) -> reply。"""
    while True:
        request = front_in.get()
        if request is None:
            return
        try:
            reply = orchestrator(request.text, request.session_id)
            front_out.put(ReplyResult(rid=request.rid, reply=reply))
        except Exception:
            front_out.put(
                ErrorResult(rid=request.rid, error=traceback.format_exc(limit=5))
            )


def backend_main(
    front_in: Any,
    front_out: Any,
    sentiment_in: Any,
    sentiment_out: Any,
    support_in: Any,
    support_out: Any,
    build_orchestrator: Callable[..., Callable[[str, str], str]],
) -> None:
    """后端进程入口。

    build_orchestrator 由各实现提供，负责把两个子进程"串"起来：
    LangGraph 版本返回一个编译后的图运行函数，Agents 版本返回一个 conductor Agent。
    """
    try:
        orchestrator = build_orchestrator(
            sentiment_in, sentiment_out, support_in, support_out
        )
        run_backend(front_in, front_out, orchestrator)
    finally:
        # 后端退出时通知进程1 / 进程2 一起退出
        sentiment_in.put(None)
        support_in.put(None)


# --------------------------------------------------------------------------- #
# 前端进程
# --------------------------------------------------------------------------- #
def _banner(title: str) -> str:
    line = "=" * 56
    return (
        f"{line}\n"
        f" {title} · 情绪陪伴四进程流水线\n"
        f" 前端 → 后端 → 进程1（情感识别）→ 进程2（鼓励/排解）\n"
        f" 输入内容回车发送；输入 exit / 退出 结束\n"
        f"{line}"
    )


def _roundtrip(
    text: str,
    request_queue: Any,
    response_queue: Any,
    timeout: float,
    session_id: str = "cli",
) -> None:
    request_queue.put(UserRequest(rid=new_rid(), text=text, session_id=session_id))
    try:
        response = response_queue.get(timeout=timeout)
    except Empty:
        print("[超时] 后端没有在限定时间内返回结果", file=sys.stderr, flush=True)
        return

    if isinstance(response, ReplyResult):
        print(f"系统 > {response.reply}\n", flush=True)
    elif isinstance(response, ErrorResult):
        print(f"系统 > [处理失败]\n{response.error}\n", file=sys.stderr, flush=True)
    else:
        print(f"系统 > [未知响应] {response!r}\n", file=sys.stderr, flush=True)


def frontend_main(
    request_queue: Any,
    response_queue: Any,
    scripted: list[str] | None = None,
    title: str = "情绪陪伴",
    timeout: float = 300.0,
) -> None:
    """前端进程入口。

    scripted 为 None 时进入交互模式（input()）；
    传入列表时按顺序处理这些文本后自动退出，方便脚本化验证。
    """
    try:
        if scripted is not None:
            for text in scripted:
                print(f"你 > {text}", flush=True)
                _roundtrip(text, request_queue, response_queue, timeout)
            print("（非交互模式结束）", flush=True)
            return

        print(_banner(title), flush=True)
        while True:
            try:
                text = input("你 > ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n再见～", flush=True)
                break
            if not text:
                continue
            if text.lower() in _EXIT_WORDS:
                print("再见～", flush=True)
                break
            _roundtrip(text, request_queue, response_queue, timeout)
    finally:
        # 通知后端退出，后端再通知进程1 / 进程2 退出
        request_queue.put(None)
