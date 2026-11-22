# -*- coding: utf-8 -*-
"""单个文件的解析 + 入库任务。"""

from __future__ import annotations

import time
import traceback
from datetime import datetime
from pathlib import Path

from ..core.config import STATUS_DONE, STATUS_FAILED, STATUS_PARSING, STATUS_PENDING
from ..core.db import session_scope
from ..orm import File
from ..services.chunker import split_markdown
from ..services.parser import parse_pdf
from ..services.vectorstore import insert_chunks


def _update(file_id: int, **fields) -> None:
    with session_scope() as session:
        record = session.query(File).filter(File.id == int(file_id)).first()
        if record is None:
            return
        for key, value in fields.items():
            setattr(record, key, value)


def process_file(file_id: int) -> bool:
    """返回是否处理成功。异常会被吞掉并写入 failed 状态。"""
    with session_scope() as session:
        record = session.query(File).filter(File.id == int(file_id)).first()
        if record is None:
            return False
        if record.status == STATUS_PARSING:
            return False
        filename = record.filename
        stored_path = record.stored_path
        stored_posix = Path(stored_path).as_posix()
        record.status = STATUS_PARSING
        record.error = None
        record.started_at = datetime.now()
        record.finished_at = None
        record.parse_seconds = 0.0

    started = time.time()
    try:
        result = parse_pdf(stored_path, file_id)
        chunks = split_markdown(result.markdown, result.md_path)
        if not chunks:
            raise RuntimeError("MinerU 解析成功，但没有切出任何文本 chunk")

        count = insert_chunks(file_id, filename, stored_posix, chunks)
        elapsed = round(time.time() - started, 1)
        _update(
            file_id,
            status=STATUS_DONE,
            page_count=result.page_count,
            chunk_count=count,
            parse_seconds=elapsed,
            error=None,
            finished_at=datetime.now(),
        )
        return True
    except Exception as exc:
        elapsed = round(time.time() - started, 1)
        detail = f"{type(exc).__name__}: {exc}"
        _update(
            file_id,
            status=STATUS_FAILED,
            parse_seconds=elapsed,
            error=detail[:2000],
            finished_at=datetime.now(),
        )
        print(f"[FAIL] file_id={file_id} {detail}")
        traceback.print_exc()
        return False
