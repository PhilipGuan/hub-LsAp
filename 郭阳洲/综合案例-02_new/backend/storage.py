# -*- coding: utf-8 -*-
"""研究报告落盘：backend/data/research/{research_id}.json，一个研究一个文件。

规格见 README.md / 任务说明书.md（T8）。
- 每个研究一个 JSON 文件，状态变化整文件覆盖写；
- 模块级 _LOCK 保证同一进程内并发写 / 读安全；
- 时间戳沿用 models.ResearchRecord 的 datetime 类型（不转字符串），
  避免 pydantic 默认不在赋值时校验导致的字段类型不一致。
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

from .config import DATA_DIR, ensure_data_dir
from .models import ResearchRecord, Status

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()


def _path(rid: str) -> Path:
    """返回某条记录的落盘路径，并确保目录存在。"""
    ensure_data_dir()
    return DATA_DIR / f"{rid}.json"


def _now() -> datetime:
    """当前 UTC 时间（带时区），直接赋给 datetime 字段。"""
    return datetime.now(timezone.utc)


def create(rid: str, topic: str) -> ResearchRecord:
    """新建一条 status=pending 的记录并落盘。"""
    now = _now()
    rec = ResearchRecord(
        research_id=rid,
        topic=topic,
        status=Status.PENDING,
        created_at=now,
        updated_at=now,
    )
    save(rec)
    logger.info("新建研究记录 %s：%s", rid, topic)
    return rec


def save(rec: ResearchRecord) -> None:
    """整文件覆盖写入一条记录。"""
    text = rec.model_dump_json(indent=2, ensure_ascii=False)
    with _LOCK:
        _path(rec.research_id).write_text(text, encoding="utf-8")


def get(rid: str) -> ResearchRecord | None:
    """按 research_id 读取记录，文件不存在返回 None。"""
    p = _path(rid)
    if not p.exists():
        return None
    with _LOCK:
        text = p.read_text(encoding="utf-8")
    return ResearchRecord.model_validate_json(text)


def list_all() -> list[ResearchRecord]:
    """扫描 DATA_DIR 下全部 *.json，按创建时间升序返回。"""
    ensure_data_dir()
    records: list[ResearchRecord] = []
    for p in DATA_DIR.glob("*.json"):
        try:
            with _LOCK:
                text = p.read_text(encoding="utf-8")
            records.append(ResearchRecord.model_validate_json(text))
        except Exception:  # noqa: BLE001 - 单条坏文件不应拖垮整个列表
            logger.warning("跳过无法解析的记录文件 %s", p.name, exc_info=True)
    records.sort(key=lambda r: r.created_at)
    return records


def update_status(rid: str, status: str, error: str | None = None) -> None:
    """读回 → 改状态 / updated_at（可选附带错误信息）→ 落盘。"""
    rec = get(rid)
    if rec is None:
        raise FileNotFoundError(f"研究记录不存在: {rid}")
    rec.status = Status(status)
    rec.updated_at = _now()
    if error is not None:
        rec.error = error
    save(rec)
    logger.info("更新研究记录 %s 状态为 %s", rid, rec.status.value)


# ------------------------------------------------------------------ 自检 demo
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    rid = "storage-selftest"
    try:
        created = create(rid, "存储模块自检主题")
        assert created.research_id == rid
        assert created.status is Status.PENDING
        assert isinstance(created.created_at, datetime), "created_at 应为 datetime"
        assert _path(rid).exists(), "create 后文件应存在"
        print(f"create  → id={created.research_id} status={created.status.value} "
              f"created_at={created.created_at.isoformat()}")

        got = get(rid)
        assert got is not None and got == created, "读回记录应与写入一致"
        assert isinstance(got.created_at, datetime), "读回 created_at 应为 datetime"
        print(f"get     → 读回一致 status={got.status.value} topic={got.topic}")

        assert get("storage-selftest-not-exist") is None
        print("get     → 不存在的 id 返回 None")

        update_status(rid, "running")
        running = get(rid)
        assert running is not None and running.status is Status.RUNNING
        assert running.updated_at >= created.updated_at, "updated_at 应被刷新"
        print(f"update  → status={running.status.value} "
              f"updated_at={running.updated_at.isoformat()}")

        update_status(rid, "failed", error="自检模拟失败")
        failed = get(rid)
        assert failed is not None and failed.status is Status.FAILED
        assert failed.error == "自检模拟失败"
        print(f"update  → status={failed.status.value} error={failed.error}")

        all_records = list_all()
        ids = [r.research_id for r in all_records]
        assert rid in ids, "list_all 应包含自检记录"
        stamps = [r.created_at for r in all_records]
        assert stamps == sorted(stamps), "list_all 应按创建时间排序"
        print(f"list_all→ 共 {len(ids)} 条，含自检记录={rid in ids}")

        print("存储自检 OK")
    finally:
        # 清理测试记录，避免污染 backend/data/research/
        p = DATA_DIR / f"{rid}.json"
        if p.exists():
            p.unlink()
        print(f"已清理测试记录 {p.name}: {not p.exists()}")
