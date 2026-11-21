# -*- coding: utf-8 -*-
"""一键启动脚本：按依赖顺序拉起四个进程，Ctrl+C 全部回收。

    进程1 情感识别 :5002
    进程2 共情回复 :5003
    后端  编排     :5000
    前端  页面     :5001

用法：
    python run_all.py
"""
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import common

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SERVICES = [
    ("进程1 情感识别", "sentiment_service.py", common.SENTIMENT_PORT, "/health"),
    ("进程2 共情回复", "reply_service.py", common.REPLY_PORT, "/health"),
    ("后端 LangGraph", "backend_service.py", common.BACKEND_PORT, "/health"),
    ("前端 聊天页", "frontend_service.py", common.FRONTEND_PORT, "/health"),
]


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def wait_healthy(port: int, path: str, timeout: float = 20.0) -> bool:
    """轮询 /health，等服务真正可用再继续。"""
    url = f"http://127.0.0.1:{port}{path}"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.4)
    return False


def main() -> int:
    busy = [f"{name} :{port}" for name, _, port, _ in SERVICES if port_in_use(port)]
    if busy:
        print("[错误] 以下端口已被占用，请先关闭旧进程：")
        for item in busy:
            print("   ", item)
        return 1

    if not common.has_api_key():
        print("[警告] 未读到 DS_API_KEY，两个 LLM 服务将返回 500。请检查 root/.env")

    print("=" * 62)
    print("  情绪疏导系统 · LangGraph 多进程版")
    print(f"  模型: {common.LLM_MODEL}   .env: {common.ENV_PATH or '未找到'}")
    print("=" * 62)

    procs = []
    try:
        for name, script, port, path in SERVICES:
            print(f"启动 {name:<14} → http://127.0.0.1:{port}")
            procs.append(subprocess.Popen([sys.executable, os.path.join(BASE_DIR, script)],
                                          cwd=BASE_DIR))
            if not wait_healthy(port, path):
                print(f"[错误] {name} 启动超时（:{port} 无响应）")
                return 1

        print("-" * 62)
        print("全部就绪，浏览器打开： http://127.0.0.1:5001")
        print("Ctrl+C 停止所有进程")
        print("-" * 62)

        while True:
            alive = [p for p in procs if p.poll() is None]
            if not alive:
                print("[错误] 所有子进程都已退出")
                return 1
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n收到 Ctrl+C，正在停止子进程…")
        return 0
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    sys.exit(main())
