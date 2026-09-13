# -*- coding: utf-8 -*-
"""Storage：on_progress 增量写盘 · 研究记录 JSONL + 单文件详情。
设计继承综合案例-02 手感：
- index.jsonl 保存每个研究 record 的 (id, ticker, status, created_at, updated_at) 行级索引；
- data/etf_records/{research_id}/record.json 完整详情；
- data/etf_records/{research_id}/exports/ 下 7 导出文件路径（批次 3 真写出）。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable

from .config import DATA_DIR
from .models import ResearchRecord

DEFAULT_DATA_DIR: Path = DATA_DIR
INDEX_JSONL = DEFAULT_DATA_DIR / "index.jsonl"


ProgressCb = Callable[[ResearchRecord], None]


def _now() -> datetime:
    return datetime.utcnow()


class Storage:
    def __init__(self, data_dir: Path | None = None, on_progress: ProgressCb | None = None) -> None:
        self.data_dir: Path = Path(data_dir or DEFAULT_DATA_DIR)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.index_path: Path = self.data_dir / "index.jsonl"
        if not self.index_path.exists():
            self.index_path.touch()
        self.on_progress: ProgressCb = on_progress or (lambda _r: None)

    # ---------- 目录/路径 ----------
    def _record_dir(self, research_id: str) -> Path:
        d = self.data_dir / research_id
        d.mkdir(parents=True, exist_ok=True)
        (d / "exports").mkdir(parents=True, exist_ok=True)
        return d

    def record_json_path(self, research_id: str) -> Path:
        return self._record_dir(research_id) / "record.json"

    def export_paths(self, research_id: str) -> dict[str, Path]:
        exports = self._record_dir(research_id) / "exports"
        return {
            "json": exports / f"{research_id}.json",
            "md": exports / f"{research_id}.md",
            "html": exports / f"{research_id}.html",
            "profile_csv": exports / f"{research_id}_profile.csv",
            "history_csv": exports / f"{research_id}_history.csv",
            "holdings_csv": exports / f"{research_id}_holdings.csv",
            "peer_csv": exports / f"{research_id}_peer_matrix.csv",
        }

    # ---------- 新建 ----------
    def new_record(self, ticker: str, style: str, peers: int) -> ResearchRecord:
        research_id = f"R_{_now().strftime('%Y%m%d-%H%M%S')}_{ticker.upper()}_{uuid.uuid4().hex[:6]}"
        created = _now()
        rec = ResearchRecord(
            research_id=research_id,
            ticker=ticker.strip().upper(),
            status="pending",
            created_at=created,
            updated_at=created,
            style=str(style or "analyst"),  # type: ignore[arg-type]
            peers_requested=max(5, min(8, int(peers or 5))),
            report=None,
            error=None,
            sources=[],
            draft=[],
        )
        self.save(rec)
        return rec

    # ---------- 持久化 ----------
    def save(self, record: ResearchRecord) -> None:
        record.updated_at = _now()
        rd = self._record_dir(record.research_id)
        tmp = (rd / "record.json.tmp")
        tmp.write_text(
            record.model_dump_json(indent=2),
            encoding="utf-8",
        )
        tmp.replace(rd / "record.json")
        self._rewrite_index_entry(record)
        try:
            self.on_progress(record)
        except Exception:  # noqa: BLE001
            pass

    def _rewrite_index_entry(self, record: ResearchRecord) -> None:
        """index.jsonl：同一 research_id 若存在就地覆盖（整文件重写，避免多行脏索引）。"""
        entries: dict[str, dict] = {}
        if self.index_path.exists():
            try:
                for line in self.index_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        rid = str(obj.get("research_id") or "")
                        if rid:
                            entries[rid] = obj
                    except json.JSONDecodeError:
                        continue
            except OSError:
                pass
        entries[record.research_id] = {
            "research_id": record.research_id,
            "ticker": record.ticker,
            "status": record.status,
            "style": record.style,
            "peers_requested": record.peers_requested,
            "created_at": record.created_at.isoformat() + "Z",
            "updated_at": record.updated_at.isoformat() + "Z",
            "error": record.error,
        }
        tmp = self.index_path.with_suffix(self.index_path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for rid in sorted(entries.keys(), reverse=True):
                f.write(json.dumps(entries[rid], ensure_ascii=False) + "\n")
        tmp.replace(self.index_path)

    # ---------- 查询 ----------
    def get(self, research_id: str) -> ResearchRecord | None:
        path = self.record_json_path(research_id)
        if not path.exists():
            return None
        try:
            raw = path.read_text(encoding="utf-8")
            return ResearchRecord.model_validate_json(raw)
        except Exception:  # noqa: BLE001
            return None

    def list(self, limit: int = 50) -> list[ResearchRecord]:
        rids: list[str] = []
        if self.index_path.exists():
            try:
                for line in self.index_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        rid = str(obj.get("research_id") or "")
                        if rid:
                            rids.append(rid)
                    except json.JSONDecodeError:
                        continue
            except OSError:
                rids = []
        out: list[ResearchRecord] = []
        for rid in rids[: max(1, int(limit))]:
            rec = self.get(rid)
            if rec is not None:
                out.append(rec)
        return out
