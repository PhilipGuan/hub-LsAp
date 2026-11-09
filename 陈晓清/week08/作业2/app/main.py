"""FastAPI 入口：提交 / 查询 / SSE 观察三个接口（需求文档第 7 节）。

服务层不含任何业务：它只把请求转给进程级单例 `ResearchOrchestrator`，再按契约把结果吐出去。
状态全在编排器手上——单进程内存态、无鉴权、不面向多实例（Demo 边界，见 CLAUDE.md）。
"""

import asyncio
import json
from collections.abc import AsyncIterator, Coroutine
from dataclasses import asdict
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, StringConstraints

from app.config import LLM_MODEL
from app.orchestrator import ResearchOrchestrator

app = FastAPI(title="深度研究助手", version="0.1.0")

# 任务状态就住在这里。单进程内存态是刻意的选择，不是待补的窟窿。
orchestrator = ResearchOrchestrator()

# `create_task()` 的返回值必须留个引用：事件循环只持弱引用，没人引用的任务可能被中途 GC 掉。
_background: set[asyncio.Task] = set()


def _spawn(coro: Coroutine[Any, Any, Any]) -> None:
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)


class ResearchRequest(BaseModel):
    """提交研究的请求体。

    `strip_whitespace` + `min_length` 一起用，是为了挡掉 " " 这种**看着非空**的主题：
    少了 strip，一个只有空格的 topic 也能开跑，白烧三次 LLM 调用。
    """

    topic: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "llm_model": LLM_MODEL}


@app.post("/api/v1/research")
async def create_research(payload: ResearchRequest) -> dict:
    """创建任务并**立刻**返回 `task_id`：研究在后台跑，进度去 SSE 看。

    先返回再跑是这套接口的关键——研究要几十秒，同步等它的接口没法边跑边推事件。
    """
    state = orchestrator.submit(payload.topic)
    _spawn(orchestrator.run(state.task_id))
    return {"task_id": state.task_id, "status": state.status}


@app.get("/api/v1/research/{task_id}")
def get_research(task_id: str) -> dict:
    """任务状态与结果（需求文档第 7 节的产物结构）。跑的过程中 `report` 还是 null。"""
    state = orchestrator.get(task_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
    return state.to_artifact()


@app.get("/api/v1/research/{task_id}/stream")
async def stream_research(task_id: str, after: int = 0) -> StreamingResponse:
    """SSE 推研究过程事件（验收项 8）。断线重连带 `?after=<最后收到的 seq>` 续传。

    `after=0` 会把已经发生过的事件先补发一遍，所以「跑完才连上来」也能拿到全过程。
    """
    if orchestrator.get(task_id) is None:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")

    async def sse() -> AsyncIterator[str]:
        async for event in orchestrator.stream(task_id, after=after):
            # SSE 帧格式：event/data 各占一行，空行结尾。data 走 json.dumps，
            # 保证正文里的换行被转义——裸换行会当场把一帧劈成两帧。
            yield f"event: {event.stage}\ndata: {json.dumps(asdict(event), ensure_ascii=False)}\n\n"

    # no-cache：SSE 是长连接，被任何一层缓存住就等于事件不推了
    return StreamingResponse(sse(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


if __name__ == "__main__":
    # 测试 demo：真实走一遍三个接口（需要 .env 里的两个 key 与网络，约 40 秒）
    import sys
    import time
    from pathlib import Path

    sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台默认 cp936，中文输出会乱码

    import httpx
    from fastapi.testclient import TestClient

    from app.orchestrator import _artifact_path  # 自测里直接查落盘文件，不另写一份路径

    def _demo() -> None:
        # 必须用 with：TestClient 不给请求单开一个事件循环，退出即销毁。
        # 后台任务是挂在那个循环上的，不用 with 的话 POST 一返回，「研究」就被当场取消。
        with TestClient(app) as client:
            # 默认 5 秒读超时扛不住 SSE：综合成文那一节十来秒不发事件，连接会被当成超时掐断
            client.timeout = httpx.Timeout(300)

            assert client.get("/health").json()["status"] == "ok"
            assert client.get("/api/v1/research/不存在").status_code == 404
            # 只有空白的主题要在入口就被挡下，不能开跑
            assert client.post("/api/v1/research", json={"topic": "   "}).status_code == 422
            print("入口自检 ok：/health / 未知任务 404 / 空白主题 422")

            started = time.perf_counter()
            created = client.post("/api/v1/research", json={"topic": "年轻人为什么爱熬夜"})
            task_id = created.json()["task_id"]
            print(f"POST → {created.json()}")

            stages: list[str] = []
            with client.stream("GET", f"/api/v1/research/{task_id}/stream") as response:
                assert response.status_code == 200
                assert response.headers["content-type"].startswith("text/event-stream")
                for line in response.iter_lines():
                    if line.startswith("data: "):
                        payload = json.loads(line[6:])
                        stages.append(payload["stage"])
                        print(f"  [{time.perf_counter() - started:6.1f}s] "
                              f"{payload['stage']:<8} {payload['message']}")

            assert stages[-1] == "done", stages  # done 必须是最后一条，客户端靠它收尾
            assert {"plan", "search", "section", "judge", "compose"} <= set(stages), stages

            result = client.get(f"/api/v1/research/{task_id}").json()
            print(f"\nGET → status={result['status']} | 轮次={result['process']['rounds']} | "
                  f"分节={len(result['report']['sections'])} | 来源={len(result['report']['sources'])} | "
                  f"置信度={result['confidence']['overall']}（{result['confidence']['level']}）")
            assert result["status"] == "succeeded", result["error"]
            chapters = ["## 摘要", "## 关键结论", "## 分节正文", "## 遗留问题", "## 参考来源", "## 置信度说明"]
            assert all(c in result["report"]["markdown"] for c in chapters), "六章节不齐"
            path: Path = _artifact_path(task_id)
            assert path.is_file(), path  # 验收项 10：落盘
            print(f"落盘: {path}")
            print("main.py 自检 ok：三个接口通 / SSE 阶段齐备且以 done 收尾 / 报告六章节齐 / 产物已落盘")

    _demo()
