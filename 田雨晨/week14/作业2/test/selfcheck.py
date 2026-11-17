"""一键跑通后端所有文件的 main 自测。

用法（仓库根目录）：
    python test/selfcheck.py            # 自动选择装好依赖的解释器（本机 py312）
    python test/selfcheck.py -v         # 每个模块都打印输出尾部
    python test/selfcheck.py services   # 只跑名字包含 services 的模块
    python test/selfcheck.py --python D:/Anaconda/envs/py312/python.exe

说明：如果当前解释器缺少 fastapi 等依赖（例如用 Anaconda base 3.13 直接跑），
会自动切换到 conda py312 再执行，避免出现“只有 6 个接口模块失败”的假故障。
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REQUIRED_MODULES = ("fastapi", "httpx", "sqlalchemy", "pydantic_settings")
MODULES = [
    "backend.app.config",
    "backend.app.common",
    "backend.app.errors",
    "backend.app.db",
    "backend.app.schemas",
    "backend.app.schemas.enums",
    "backend.app.schemas.batch",
    "backend.app.schemas.content",
    "backend.app.schemas.qa",
    "backend.app.schemas.events",
    "backend.app.models.tables",
    "backend.app.clients.storage",
    "backend.app.clients.queue",
    "backend.app.clients.agent",
    "backend.app.events.sse",
    "backend.app.services.asset",
    "backend.app.services.outbox",
    "backend.app.services.upload",
    "backend.app.services.document",
    "backend.app.services.qa",
    "backend.app.api.deps",
    "backend.app.api.upload",
    "backend.app.api.documents",
    "backend.app.api.qa",
    "backend.app.api.assets",
    "backend.app.main",
    "backend.agent.config",
    "backend.agent.prompt_loader",
    "backend.agent.llm",
    "backend.agent.embedding",
    "backend.agent.fixtures",
    "backend.agent.tools.text_search",
    "backend.agent.tools.image_load",
    "backend.agent.tools.image_caption",
    "backend.agent.tools.citation_builder",
    "backend.agent.graph.planner",
    "backend.agent.graph.synthesizer",
    "backend.workflow",
    "backend.agent.service",
    # parse-worker 用连字符目录（不可作为包导入），按脚本直接运行
    "parse-worker/mineru_runner.py",
    "parse-worker/chunker.py",
    "parse-worker/indexer.py",
    "parse-worker/worker.py",
]


def missing_modules(python: str) -> list[str]:
    """用目标解释器检查依赖，返回缺失的模块名；解释器不可用时返回占位说明。"""
    code = "import importlib.util as u\n" f"print(','.join(n for n in {REQUIRED_MODULES!r} if u.find_spec(n) is None))\n"
    try:
        result = subprocess.run([python, "-c", code], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return ["<解释器不可用>"]
    if result.returncode != 0:
        return ["<解释器不可用>"]
    return [name for name in result.stdout.strip().split(",") if name]


def python_version(python: str) -> str:
    """查询解释器版本号，仅用于启动日志。"""
    try:
        result = subprocess.run(
            [python, "-c", "import sys;print(sys.version.split()[0])"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return "?"
    return result.stdout.strip() or "?"


def candidate_pythons() -> list[str]:
    """候选解释器：环境变量 > 当前解释器同目录的 conda envs/py312 > 常见安装路径。"""
    candidates: list[str] = []
    if os.environ.get("RAG_PYTHON"):
        candidates.append(os.environ["RAG_PYTHON"])
    executable = Path(sys.executable).resolve()
    for parent in (executable.parent, executable.parent / "envs"):
        candidates.append(str(parent / "py312" / "python.exe"))
        candidates.append(str(parent / "envs" / "py312" / "python.exe"))
    for prefix in ("anaconda3", "miniconda3", "Anaconda3", "miniconda3"):
        candidates.append(str(Path.home() / prefix / "envs" / "py312" / "python.exe"))
    candidates.append(r"D:\Anaconda\envs\py312\python.exe")
    unique: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in unique:
            unique.append(candidate)
    return unique


def choose_python(explicit: str | None) -> tuple[str, list[str]]:
    """确定用哪个解释器跑自测，返回 (解释器, 当前解释器缺失的依赖)。"""
    current_missing = missing_modules(sys.executable)
    if explicit:
        return explicit, current_missing
    if not current_missing:
        return sys.executable, current_missing
    for candidate in candidate_pythons():
        if Path(candidate).is_file() and not missing_modules(candidate):
            return candidate, current_missing
    return sys.executable, current_missing


def run_module(python: str, module: str, env: dict[str, str]) -> subprocess.CompletedProcess:
    """用指定解释器跑一个模块（module）或脚本（以 .py 结尾）的 main 自测。"""
    command = [python, module] if module.endswith(".py") else [python, "-m", module]
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def tail(text: str, lines: int = 8) -> str:
    """取输出末尾若干行并缩进，失败时打印关键信息。"""
    rows = [row for row in text.strip().splitlines() if row.strip()]
    return "\n".join("    " + row for row in rows[-lines:])


def main() -> int:
    """入口：选解释器 → 逐模块跑 main → 汇总 PASS/FAIL，返回进程退出码。"""
    parser = argparse.ArgumentParser(description="跑通后端所有模块的 main 自测")
    parser.add_argument("-v", "--verbose", action="store_true", help="打印每个模块的输出尾部")
    parser.add_argument("--python", default=None, help="指定解释器路径（默认自动选择）")
    parser.add_argument("keyword", nargs="?", default="", help="只跑模块名包含该关键字的模块")
    args = parser.parse_args()

    python, current_missing = choose_python(args.python)
    if args.python:
        if missing_modules(args.python):
            print(f"[selfcheck] 指定的解释器 {args.python} 缺少依赖: {', '.join(missing_modules(args.python))}")
            print("[selfcheck] 请先 pip install -r backend/requirements.txt，或改用 --python 指向 py312")
            return 1
    elif current_missing:
        if python == sys.executable:
            print(f"[selfcheck] 当前解释器缺少依赖: {', '.join(current_missing)}")
            print(
                "[selfcheck] 未找到装好依赖的解释器。请用 conda 环境 py312 运行：\n"
                "    D:\\Anaconda\\envs\\py312\\python.exe test/selfcheck.py\n"
                "或先安装依赖：pip install -r backend/requirements.txt"
            )
            return 1
        print(f"[selfcheck] 当前解释器 {sys.executable} 缺少: {', '.join(current_missing)}")
        print(f"[selfcheck] 已自动切换到: {python}（Python {python_version(python)}）")
    else:
        print(f"[selfcheck] 解释器: {python}（Python {python_version(python)}）")

    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    env["APP_ENV"] = "local"

    modules = [m for m in MODULES if args.keyword.lower() in m.lower()]
    if not modules:
        print(f"没有匹配 {args.keyword!r} 的模块")
        return 1

    print(f"[selfcheck] 共 {len(modules)} 个模块")
    failed: list[tuple[str, subprocess.CompletedProcess]] = []
    for module in modules:
        result = run_module(python, module, env)
        ok = result.returncode == 0
        print(("PASS" if ok else "FAIL"), module)
        if not ok:
            failed.append((module, result))
        if args.verbose or not ok:
            output = result.stdout + result.stderr
            if output.strip():
                print(tail(output))

    print("-" * 60)
    if failed:
        print(f"[selfcheck] 失败 {len(failed)} / {len(modules)}")
        for module, _ in failed:
            print("  失败模块:", module)
        return 1
    print(f"[selfcheck] 全部 {len(modules)} 个模块自检通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
