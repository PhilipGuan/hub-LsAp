"""研究编排：后台执行，on_progress 中间结果逐步落盘，最终落盘。

状态机 pending -> running -> completed / failed；每完成一轮（含规划）把中间
`process`（含 steps）、`draft`（此处只传长度信息，供轮询）与 `sources` 写盘，
status 保持 running；任何异常保留已写入的中间结果并置为 failed。
"""
import logging
from datetime import datetime

from . import storage
from .engine import DeepResearch
from .models import ResearchRecord

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


async def run_research(
    rid: str,
    topic: str,
    max_rounds: int | None = None,
    persist: bool = True,
) -> ResearchRecord:
    """发起一次研究：落初始 running 记录，逐步写中间态，最终写 completed/failed。"""
    created_at = _now()
    if persist:
        storage.save(ResearchRecord(id=rid, status="running", topic=topic, created_at=created_at, updated_at=created_at))

    async def on_progress(process, _draft_text, sources):
        # 同步或异步都被 engine 感知；这里把中间态写盘（status 保持 running）
        if persist:
            storage.save(ResearchRecord(
                id=rid, status="running", topic=topic,
                process=process, sources=sources,
                created_at=created_at, updated_at=_now(),
            ))

    engine = DeepResearch(max_rounds=max_rounds)
    try:
        logger.info("研究任务开始 rid=%s topic=%r", rid, topic)
        result = await engine.run(topic, on_progress=on_progress)
        final = ResearchRecord(
            id=rid, status="completed", topic=topic,
            report=result.report, report_html=result.report_html,
            sources=result.sources, process=result.process,
            confidence=result.confidence,
            created_at=created_at, updated_at=_now(),
        )
        if persist:
            storage.save(final)
        logger.info("研究任务完成 rid=%s", rid)
        return final
    except Exception as e:
        logger.exception("研究任务失败 rid=%s", rid)
        base = storage.load(rid) if persist else None
        rec = base or ResearchRecord(id=rid, topic=topic, created_at=created_at)
        rec.status = "failed"
        rec.error = str(e)
        rec.updated_at = _now()
        if persist:
            storage.save(rec)
        raise


if __name__ == "__main__":
    import asyncio
    import logging
    import uuid

    from .config import settings

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    async def _demo():
        print("=== 完整研究 + 落盘（端到端） ===")
        rid = uuid.uuid4().hex[:12]
        final = await run_research(rid, "天空为什么是蓝色的")
        stored = storage.load(rid)
        print("status:", stored.status)
        print("报告标题:", stored.report.title)
        print("来源数:", len(stored.sources), "HTML长度:", len(stored.report_html or ""))
        print("研究过程 steps:", len(stored.process.steps), "轮次:", stored.process.iterations)

    if not settings.deepseek_api_key:
        print("未配置 DEEPSEEK_API_KEY/BOCHA_API_KEY（.env），跳过端到端 demo。")
    else:
        asyncio.run(_demo())