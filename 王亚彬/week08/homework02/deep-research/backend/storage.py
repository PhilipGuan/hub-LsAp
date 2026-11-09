"""任务落盘：JSON 文件读写。"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from backend import config
from backend.models import ResearchTask

logger = logging.getLogger(__name__)


def _path(task_id: str) -> Path:
    return config.DATA_DIR / f"{task_id}.json"


def save(task: ResearchTask) -> Path:
    p = _path(task.id)
    p.write_text(task.model_dump_json(indent=2), encoding="utf-8")
    return p


def load(task_id: str) -> ResearchTask | None:
    p = _path(task_id)
    if not p.exists():
        return None
    try:
        return ResearchTask.model_validate_json(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.error("读取任务失败（%s）：%s", task_id, exc)
        return None


def list_tasks(limit: int = 50) -> list[dict]:
    files = sorted(config.DATA_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    out = []
    for f in files[:limit]:
        try:
            t = ResearchTask.model_validate_json(f.read_text(encoding="utf-8"))
            out.append(t.summary_dict())
        except Exception:  # noqa: BLE001
            continue
    return out


if __name__ == "__main__":
    from backend.config import setup_logging

    setup_logging()
    t = ResearchTask(topic="存储测试", status="completed")
    p = save(t)
    print("已写入：", p)
    back = load(t.id)
    print("读回 topic：", back.topic if back else None)
    print("任务数：", len(list_tasks()))
