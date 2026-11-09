"""落盘：backend/data/research/{id}.json 读写（threading.Lock 保护）。

状态机 pending -> running -> completed / failed 由调用方维护，这里只负责
`ResearchRecord` 的原子读写与列表。
"""
import json
import logging
import threading
from pathlib import Path

from .config import PROJECT_ROOT, settings
from .models import ResearchRecord

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()

_resolve_root = (
    Path(settings.data_dir)
    if Path(settings.data_dir).is_absolute()
    else PROJECT_ROOT / settings.data_dir
)
DATA_ROOT = _resolve_root / "research"


def _path(rid: str) -> Path:
    return DATA_ROOT / f"{rid}.json"


def save(record: ResearchRecord) -> None:
    """写盘一条研究记录（原子：先写临时文件再替换）。"""
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    tmp = DATA_ROOT / f".{record.id}.tmp"  # 先写临时文件再替换
    with _LOCK:
        tmp.write_text(
            json.dumps(record.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(_path(record.id))
    logger.debug("已落盘 %s", _path(record.id))


def load(rid: str) -> ResearchRecord | None:
    """读一条记录；不存在返回 None。"""
    path = _path(rid)
    if not path.exists():
        return None
    with _LOCK:
        try:
            return ResearchRecord.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as e:  # pragma: no cover
            logger.error("读取研究记录失败 %s: %s", path, e)
            return None


def list_all() -> list[ResearchRecord]:
    """列出全部记录，按 updated_at 倒序。"""
    records: list[ResearchRecord] = []
    with _LOCK:
        if not DATA_ROOT.exists():
            return records
        for path in DATA_ROOT.glob("*.json"):
            try:
                obj = ResearchRecord.model_validate_json(path.read_text(encoding="utf-8"))
                records.append(obj)
            except Exception as e:
                logger.warning("跳过损坏记录 %s: %s", path, e)
    records.sort(key=lambda r: r.updated_at, reverse=True)
    return records


if __name__ == "__main__":
    import logging
    from datetime import datetime

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    print("=== 落盘读写自检 ===")
    rid = f"test-{datetime.now().strftime('%H%M%S%f')}"
    rec = ResearchRecord(
        id=rid, status="completed",
        topic="自检主题",
        report_html="<html>ok</html>",
        sources=[{"url": "https://a.example/", "title": "A"}],
        created_at=datetime.now().isoformat(timespec="seconds"),
        updated_at=datetime.now().isoformat(timespec="seconds"),
    )
    try:
        save(rec)
        back = load(rid)
        assert back is not None and back.id == rid and back.report_html == "<html>ok</html>"
        ids = [r.id for r in list_all()]
        assert rid in ids
        print("save/load/list 均通过")
        print("落盘路径:", _path(rid))
    finally:
        # 自检不污染真实数据
        p = _path(rid)
        if p.exists():
            p.unlink()
        left = _path(rid)
        assert not left.exists()
        print("测试记录已清理")