# -*- coding: utf-8 -*-
"""研究编排：后台执行研究引擎 + 中间结果逐步落盘。

规格见 README.md / 任务说明书.md（T9）。
把 DeepResearch（引擎）与 storage（落盘）串起来：

1. 开始先把记录置 running；
2. engine 每完成一轮通过 on_progress 回调把中间结果（process / draft / sources）
   写回记录（status 保持 running），前端轮询即可实时看到进度；
3. 成功把 DeepResearchResult 全部产物写入记录并置 completed；
4. 异常置 failed 并记录 error，**已写入的中间结果保留**（不清空记录）。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from . import storage
from .engine import DeepResearch
from .models import DraftBlock, ResearchProcess, Source, Status

logger = logging.getLogger(__name__)


async def run_research(rid: str, topic: str) -> None:
    """后台研究任务：引擎编排 + 中间结果逐步落盘。"""
    logger.info("研究任务开始: rid=%s topic=%r", rid, topic)
    storage.update_status(rid, "running")
    engine = DeepResearch(topic)

    async def on_progress(snapshot: dict) -> None:
        """每轮结束的回调：把当轮快照写回记录，让轮询能看到中间结果。"""
        rec = storage.get(rid)
        if rec is None:
            logger.warning("写中间结果时记录已不存在，跳过: rid=%s", rid)
            return
        # 快照是 model_dump 出来的 dict，需 model_validate 还原成模型对象，
        # 否则字段里会存进 dict（pydantic 默认不在赋值时校验）。
        rec.process = ResearchProcess.model_validate(snapshot["process"])
        rec.draft = [DraftBlock.model_validate(b) for b in snapshot["draft"]]
        rec.sources = [Source.model_validate(s) for s in snapshot["sources"]]
        rec.status = Status.RUNNING
        rec.updated_at = datetime.now(timezone.utc)
        storage.save(rec)
        logger.info(
            "中间结果写盘: rid=%s steps=%d 正文=%d 段 来源=%d 条",
            rid,
            len(rec.process.steps),
            len(rec.draft),
            len(rec.sources),
        )

    try:
        result = await engine.run(on_progress=on_progress)
    except Exception as exc:  # noqa: BLE001 - 任何异常都要落 failed，且保留中间结果
        logger.exception("研究任务失败: rid=%s", rid)
        storage.update_status(rid, "failed", error=str(exc))
        return

    rec = storage.get(rid)
    if rec is None:
        logger.warning("研究完成但记录已不存在，跳过写盘: rid=%s", rid)
        return
    rec.report = result.report
    rec.report_html = result.report_html
    rec.sources = result.sources
    rec.draft = result.draft
    rec.process = result.process
    rec.confidence = result.confidence
    storage.save(rec)
    storage.update_status(rid, "completed")
    logger.info(
        "研究任务完成: rid=%s 分节=%d 来源=%d 置信度=%s",
        rid,
        len(result.report.sections),
        len(result.sources),
        result.confidence.overall if result.confidence else "-",
    )


# ------------------------------------------------------------------ 自检 demo
if __name__ == "__main__":
    import asyncio
    from uuid import uuid4

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    demo_topic = "2026 年主流 Agent 框架对比"
    demo_rid = uuid4().hex[:12]
    storage.create(demo_rid, demo_topic)
    print(f"已创建研究记录 rid={demo_rid}，开始执行完整研究（需要网络，耗时数分钟）…")

    asyncio.run(run_research(demo_rid, demo_topic))

    record = storage.get(demo_rid)
    assert record is not None, "研究结束后应能读回记录"
    print("=" * 60)
    print(f"rid    = {record.research_id}")
    print(f"topic  = {record.topic}")
    print(f"status = {record.status.value}")
    print(f"分节数 = {len(record.report.sections) if record.report else 0}")
    print(f"来源数 = {len(record.sources)}")
    if record.confidence:
        print(f"置信度 = {record.confidence.overall}（截至 {record.confidence.info_cutoff}）")
    print(f"迭代轮 = {record.process.iterations}，过程步骤 {len(record.process.steps)} 步")

    assert record.status is Status.COMPLETED, f"期望 completed，实际 {record.status}"
    assert record.report is not None and record.report.sections, "报告分节不应为空"
    assert record.report_html and record.report_html.strip(), "HTML 不应为空"
    assert record.sources, "来源不应为空"
    print("研究编排自检 OK")
