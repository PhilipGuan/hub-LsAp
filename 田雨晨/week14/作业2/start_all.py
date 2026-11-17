"""一键启动脚本：后端(8000) + 解析工作器 + 问答智能体(8100) + 前端(3000)。

在仓库根目录执行：

    python start_all.py                  # 本地默认：离线假模型/假向量，不调用真实模型
    python start_all.py --real-models     # 智能体与工作器使用真实模型（密钥读 deploy/.env）
    python start_all.py --no-agent        # 只起后端 + 解析工作器 + 前端（后端用离线假智能体）
    python start_all.py --no-frontend     # 只起后端 + 解析工作器 + 智能体
    python start_all.py --python D:/Anaconda/envs/py312/python.exe   # 指定解释器

行为说明：
- 自动选择装好依赖的解释器（默认 py312），自动安装缺失的前端依赖（首次运行）；
- 启动顺序：后端 → 解析工作器 → 智能体 → 前端，每步等待就绪后才继续；
- 所有子进程日志写入 `.run/logs/<服务名>.log`；进程号写入 `.run/pids.json`；
- 按 Ctrl+C 停止全部子进程；某个服务异常退出时会打印日志尾部并整体退出。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUN_DIR = ROOT / ".run"
LOG_DIR = RUN_DIR / "logs"
PID_FILE = RUN_DIR / "pids.json"

BACKEND_PORT = 8000
AGENT_PORT = 8100
FRONTEND_PORT = 3000
BACKEND_URL = f"http://127.0.0.1:{BACKEND_PORT}"
AGENT_URL = f"http://127.0.0.1:{AGENT_PORT}"
FRONTEND_URL = f"http://localhost:{FRONTEND_PORT}"  # 用 localhost 打开：Next dev 对 127.0.0.1 来源的 dev 资源有跨源限制（已在 next.config.ts 放行，但仍推荐 localhost）

# 后端/智能体运行所需的第三方包（缺了就别用当前解释器）
PROJECT_DEPS = ("fastapi", "httpx", "sqlalchemy", "pydantic_settings", "uvicorn")
FALLBACK_PYTHONS = (
    r"D:\Anaconda\envs\py312\python.exe",
    r"C:\ProgramData\Anaconda3\envs\py312\python.exe",
)

BACKEND_WAIT_S = 90.0
AGENT_WAIT_S = 60.0
FRONTEND_WAIT_S = 180.0


def log(message: str) -> None:
    """统一的启动日志（带 [start] 前缀，方便和子进程日志区分）。"""
    print(f"[start] {message}", flush=True)


def python_has_deps(python: str) -> bool:
    """检查解释器是否装齐后端依赖。"""
    code = (
        "import importlib.util as u;"
        f"print(all(u.find_spec(n) is not None for n in {PROJECT_DEPS!r}))"
    )
    try:
        result = subprocess.run([python, "-c", code], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and result.stdout.strip() == "True"


def pick_python(explicit: str | None) -> str:
    """优先用显式指定/环境变量/当前解释器，都不满足依赖时回退到本机 py312。"""
    candidates = [explicit, os.environ.get("RAG_PYTHON"), sys.executable, *FALLBACK_PYTHONS]
    unique: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in unique:
            unique.append(candidate)
    for candidate in unique:
        if Path(candidate).is_file() and python_has_deps(candidate):
            return candidate
    raise SystemExit(
        "[start] 没找到装好后端依赖的 Python。请先执行：\n"
        "    D:\\Anaconda\\envs\\py312\\python.exe -m pip install -r backend/requirements.txt"
    )


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def wait_http(url: str, timeout_s: float, label: str) -> bool:
    """轮询 URL 直到返回 2xx/3xx；超时返回 False。"""
    log(f"等待 {label} 就绪：{url}")
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if 200 <= response.status < 400:
                    log(f"{label} 已就绪")
                    return True
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
            pass
        time.sleep(0.5)
    log(f"{label} 在 {timeout_s:.0f}s 内没有就绪")
    return False


def start_service(name: str, command: list[str], env: dict[str, str], cwd: Path | None = None) -> subprocess.Popen:
    """启动一个子进程：日志写文件，Windows 下独立进程组便于整组结束。"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{name}.log"
    log_file = open(log_path, "wb")  # 二进制写，避免子进程输出编码问题
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    log(f"启动 {name}: {' '.join(command)}")
    process = subprocess.Popen(
        command,
        cwd=str(cwd or ROOT),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        creationflags=creationflags,
    )
    log_file.close()  # 子进程已继承句柄，父进程可以关掉
    return process


def log_tail(name: str, lines: int = 20) -> str:
    """读取某个服务日志的末尾几行，启动失败时打印给用户。"""
    log_path = LOG_DIR / f"{name}.log"
    if not log_path.exists():
        return "（没有日志）"
    content = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(content[-lines:]) or "（日志为空）"


def stop_all(processes: dict[str, subprocess.Popen]) -> None:
    """逆序结束所有子进程（Windows 用 taskkill /T 连子进程一起杀）。"""
    for name in reversed(list(processes)):
        process = processes[name]
        if process.poll() is not None:
            continue
        log(f"停止 {name}（pid={process.pid}）")
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
            )
        else:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()


def write_pid_file(processes: dict[str, subprocess.Popen]) -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(
        json.dumps({name: process.pid for name, process in processes.items()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def ensure_frontend_deps() -> str | None:
    """返回可用的 npm 命令；没有 npm 或安装失败则返回 None（跳过前端）。"""
    npm = shutil.which("npm")
    if npm is None:
        log("没找到 npm，跳过前端（可单独执行 cd frontend && npm install）")
        return None
    if not (ROOT / "frontend" / "node_modules").is_dir():
        log("首次运行：安装前端依赖（npm install）...")
        try:
            subprocess.run([npm, "install"], cwd=str(ROOT / "frontend"), check=True)
        except (OSError, subprocess.SubprocessError) as error:
            log(f"前端依赖安装失败，跳过前端：{error}")
            return None
    return npm


def build_services(args: argparse.Namespace, python: str, npm: str | None) -> list[dict]:
    """根据参数组装要启动的服务列表（顺序即启动顺序，后端必须最先）。"""
    base_env = os.environ.copy()
    base_env["PYTHONIOENCODING"] = "utf-8"
    base_env.setdefault("APP_ENV", "local")

    services: list[dict] = []

    backend_env = dict(base_env)
    if args.no_agent:
        backend_env["AGENT_STUB"] = "1"  # 不起智能体服务时，后端用离线假智能体兜底问答
    services.append(
        {
            "name": "backend",
            "command": [
                python,
                "-m",
                "uvicorn",
                "backend.app.main:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(BACKEND_PORT),
            ],
            "env": backend_env,
            "health": f"{BACKEND_URL}/health",
            "wait": BACKEND_WAIT_S,
        }
    )

    worker_env = dict(base_env)
    if not args.real_models:
        worker_env["WORKER_STUB_MODELS"] = "1"  # 假向量维度与智能体假模型一致，离线也能检索
    services.append(
        {
            "name": "worker",
            "command": [python, str(ROOT / "parse-worker" / "worker.py"), "--serve"],
            "env": worker_env,
        }
    )

    if not args.no_agent:
        agent_env = dict(base_env)
        if not args.real_models:
            agent_env["AGENT_STUB_LLM"] = "1"
        services.append(
            {
                "name": "agent",
                "command": [
                    python,
                    "-m",
                    "uvicorn",
                    "backend.agent.service:create_app",
                    "--factory",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(AGENT_PORT),
                ],
                "env": agent_env,
                "health": f"{AGENT_URL}/health",
                "wait": AGENT_WAIT_S,
            }
        )

    if not args.no_frontend and npm is not None:
        frontend_env = dict(base_env)
        frontend_env["NEXT_PUBLIC_API_BASE_URL"] = BACKEND_URL
        # Windows 上 npm 是 .cmd，必须经由 cmd /c 启动；这样 taskkill /T 也能连 node 子进程一起结束
        command = (
            ["cmd", "/c", npm, "run", "dev"]
            if os.name == "nt"
            else [npm, "run", "dev"]
        )
        services.append(
            {
                "name": "frontend",
                "command": command,
                "env": frontend_env,
                "cwd": ROOT / "frontend",  # npm run dev 必须在 frontend/ 下执行
                "health": f"{FRONTEND_URL}/",
                "wait": FRONTEND_WAIT_S,
            }
        )
    return services


def main() -> int:
    cli = argparse.ArgumentParser(description="一键启动：后端 + 解析工作器 + 智能体 + 前端")
    cli.add_argument("--python", default=None, help="指定 Python 解释器（默认自动选择装好依赖的）")
    cli.add_argument("--real-models", action="store_true", help="智能体/工作器使用真实模型（读 deploy/.env）")
    cli.add_argument("--no-agent", action="store_true", help="不启动智能体服务，后端使用离线假智能体")
    cli.add_argument("--no-frontend", action="store_true", help="不启动前端")
    args = cli.parse_args()

    python = pick_python(args.python)
    log(f"Python 解释器：{python}")

    npm = None if args.no_frontend else ensure_frontend_deps()
    services = build_services(args, python, npm)

    # 先检查端口，避免启动到一半才发现被占用
    for service in services:
        if "health" not in service:
            continue
        port = BACKEND_PORT if service["name"] == "backend" else (
            AGENT_PORT if service["name"] == "agent" else FRONTEND_PORT
        )
        if port_in_use(port):
            log(f"端口 {port} 已被占用（{service['name']}），请先停止占用进程再运行：")
            if os.name == "nt":
                log(f"  netstat -ano | findstr :{port}      # 查 PID")
                log("  taskkill /PID <pid> /T /F           # 结束该进程及其子进程")
            else:
                log(f"  lsof -i :{port}                     # 查 PID")
                log("  kill <pid>                          # 结束该进程")
            log("  提示：上次若直接关闭终端窗口，子进程可能残留；用 Ctrl+C 退出才会一并清理。")
            return 1

    processes: dict[str, subprocess.Popen] = {}
    try:
        for service in services:
            process = start_service(
                service["name"],
                service["command"],
                service["env"],
                service.get("cwd"),
            )
            processes[service["name"]] = process
            if "health" in service:
                if not wait_http(service["health"], service["wait"], service["name"]):
                    log(f"{service['name']} 启动失败，日志尾部：")
                    print(log_tail(service["name"]), flush=True)
                    return 1
        write_pid_file(processes)

        log("全部服务已启动：")
        log(f"  前端页面   {FRONTEND_URL}" if not args.no_frontend else "  前端       未启动")
        log(f"  接口文档   {BACKEND_URL}/docs")
        log(f"  后端健康   {BACKEND_URL}/health")
        if not args.no_agent:
            log(f"  智能体     {AGENT_URL}/health")
        log(f"  运行模式   {'真实模型' if args.real_models else '离线假模型（不调用真实模型）'}")
        log("日志目录   .run/logs/，按 Ctrl+C 停止全部服务")

        # 常驻：任一关键服务退出就打印日志并整体退出（避免“看起来在跑其实挂了”）
        while True:
            time.sleep(1.0)
            for name, process in processes.items():
                code = process.poll()
                if code is not None:
                    log(f"{name} 进程已退出（exit={code}），日志尾部：")
                    print(log_tail(name), flush=True)
                    return 1
    except KeyboardInterrupt:
        log("收到 Ctrl+C，正在停止全部服务...")
        return 0
    finally:
        stop_all(processes)
        PID_FILE.unlink(missing_ok=True)
        log("已全部停止")


if __name__ == "__main__":
    sys.exit(main())
