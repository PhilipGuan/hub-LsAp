"""PDF parsing, image understanding, and chunk indexing pipeline."""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import time
import zipfile
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from .chunker import (
    build_chunks,
    collect_image_refs,
    document_title,
    load_structured,
    page_count,
)
from .config import (
    CAPTION_CONCURRENCY,
    CAPTION_IMAGES,
    CAPTION_TIMEOUT_SECONDS,
    MINERU_BIN,
    MINERU_FALLBACK_TIER,
    MINERU_TIMEOUT_SECONDS,
    MINERU_TIER,
    PARSED_DIR,
)
from .db import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PARSING,
    now_iso,
    replace_chunks,
    update_document,
)
from .kafka_bus import KafkaPublisher
from .llm import caption_image

logger = logging.getLogger(__name__)


def _safe_extract(zip_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            name = info.filename.replace("\\", "/")
            if not name or name.startswith("/"):
                continue
            target = (destination / name).resolve()
            try:
                target.relative_to(root)
            except ValueError:
                raise RuntimeError(f"unsafe zip entry: {info.filename}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)


def _find_content_root(raw_dir: Path) -> Path | None:
    queue = [raw_dir]
    seen: set[Path] = set()
    while queue:
        candidate = queue.pop(0)
        if candidate in seen:
            continue
        seen.add(candidate)
        if (candidate / "markdown.md").exists() or (
            candidate / "structured_content.json"
        ).exists():
            return candidate
        try:
            queue.extend(
                child for child in candidate.iterdir() if child.is_dir() and child.name != "__MACOSX"
            )
        except OSError:
            continue
    return None


def _copy_parsed_tree(source: Path, destination: Path) -> None:
    for item in source.iterdir():
        if item.name in {"_raw", "__MACOSX"}:
            continue
        target = destination / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)


def run_mineru(
    pdf_path: Path, document_id: str, requested_tier: str | None = None
) -> tuple[Path, str]:
    """Run MinerU with a reliability fallback; return (parsed_dir, tier_used)."""
    parsed_dir = PARSED_DIR / document_id
    if parsed_dir.exists():
        shutil.rmtree(parsed_dir)
    parsed_dir.mkdir(parents=True, exist_ok=True)

    tiers: list[str] = []
    for tier in (requested_tier or MINERU_TIER, MINERU_FALLBACK_TIER):
        if tier and tier not in tiers:
            tiers.append(tier)
    if "basic" not in tiers:
        tiers.append("basic")

    log_lines: list[str] = []
    for tier in tiers:
        zip_path = parsed_dir / f"mineru-{tier}.zip"
        if zip_path.exists():
            zip_path.unlink()
        command = [
            MINERU_BIN,
            "parse",
            str(pdf_path),
            "--format",
            "zip",
            "--tier",
            tier,
            "-o",
            str(zip_path),
        ]
        log_lines.append(f"$ {' '.join(command)}")
        logger.info("running MinerU (tier=%s) for %s", tier, pdf_path.name)
        started = time.monotonic()
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=MINERU_TIMEOUT_SECONDS,
                cwd=str(parsed_dir),
            )
            output = (completed.stdout or "") + (completed.stderr or "")
            log_lines.append(output[-12000:])
            success = completed.returncode == 0 and zip_path.exists()
        except subprocess.TimeoutExpired:
            success = False
            log_lines.append(f"ERROR: MinerU timed out after {MINERU_TIMEOUT_SECONDS}s")
        except Exception as exc:
            success = False
            log_lines.append(f"ERROR: {exc}")

        if success:
            raw_dir = parsed_dir / "_raw"
            raw_dir.mkdir()
            try:
                _safe_extract(zip_path, raw_dir)
                content_root = _find_content_root(raw_dir)
                if content_root is None:
                    raise RuntimeError("MinerU zip did not contain markdown/structured content")
                _copy_parsed_tree(content_root, parsed_dir)
            finally:
                shutil.rmtree(raw_dir, ignore_errors=True)
                zip_path.unlink(missing_ok=True)
            (parsed_dir / "mineru.log").write_text(
                "\n".join(log_lines), encoding="utf-8"
            )
            elapsed = time.monotonic() - started
            logger.info("MinerU succeeded (tier=%s) in %.1fs", tier, elapsed)
            return parsed_dir, tier

        log_lines.append(f"ERROR: tier {tier} did not produce a usable zip")
        logger.warning("MinerU tier %s failed for %s", tier, pdf_path.name)
        zip_path.unlink(missing_ok=True)

    (parsed_dir / "mineru.log").write_text("\n".join(log_lines), encoding="utf-8")
    raise RuntimeError(
        "MinerU 解析失败，请查看 data/parsed/"
        f"{document_id}/mineru.log"
    )


def extract_pdf_text(path: Path) -> list[str]:
    try:
        reader = PdfReader(str(path))
        return [(page.extract_text() or "").strip() for page in reader.pages]
    except Exception as exc:
        logger.warning("pypdf fallback failed for %s: %s", path, exc)
        return []


async def _caption_refs(
    refs: list[Any],
    *,
    title: str,
    parsed_dir: Path,
    document_id: str,
    publisher: KafkaPublisher,
) -> dict[str, str]:
    if not CAPTION_IMAGES or not refs:
        if refs:
            document = update_document(
                document_id,
                progress=62,
                stage=f"跳过图片语义描述（{len(refs)} 张）",
            )
            if document:
                await publisher.publish_document(document)
        return {}

    semaphore = asyncio.Semaphore(max(1, CAPTION_CONCURRENCY))
    lock = asyncio.Lock()
    done = 0

    async def one(ref: Any) -> tuple[str, str]:
        nonlocal done
        async with semaphore:
            try:
                text = await asyncio.wait_for(
                    caption_image(
                        parsed_dir / ref.rel_path,
                        document_title=title,
                        heading=ref.heading,
                    ),
                    timeout=CAPTION_TIMEOUT_SECONDS,
                )
            except Exception as exc:
                logger.warning("caption timeout/error for %s: %s", ref.rel_path, exc)
                text = ""
        async with lock:
            done += 1
            progress = 48 + int(30 * done / max(len(refs), 1))
            document = update_document(
                document_id,
                progress=progress,
                stage=f"理解插图 {done}/{len(refs)}",
            )
            if document:
                await publisher.publish_document(document)
        return ref.rel_path, text

    values = await asyncio.gather(*(one(ref) for ref in refs))
    return {rel: text for rel, text in values if text}


async def ingest_document(
    document: dict[str, Any], publisher: KafkaPublisher
) -> dict[str, Any]:
    """The worker-side pipeline. Status changes are persisted and announced."""
    document_id = document["id"]
    pdf_path = Path(document["stored_path"])
    started = time.monotonic()

    document = update_document(
        document_id,
        status=STATUS_PARSING,
        progress=6,
        stage="MinerU 正在解析 PDF",
        error=None,
    ) or document
    await publisher.publish_document(document)

    try:
        if not pdf_path.is_file():
            raise FileNotFoundError(f"源文件不存在：{pdf_path}")

        parsed_dir, used_tier = await asyncio.to_thread(
            run_mineru, pdf_path, document_id, document.get("tier")
        )
        document = update_document(
            document_id,
            progress=45,
            stage="MinerU 解析完成，整理版面",
            tier=used_tier,
        ) or document
        await publisher.publish_document(document)

        structured = load_structured(parsed_dir)
        title = document_title(structured, document["filename"])
        total_pages = page_count(structured)
        image_refs = collect_image_refs(structured, parsed_dir)

        captions = await _caption_refs(
            image_refs,
            title=title,
            parsed_dir=parsed_dir,
            document_id=document_id,
            publisher=publisher,
        )

        document = update_document(
            document_id,
            progress=82,
            stage="建立文本与图像索引",
            title=title,
            page_count=total_pages,
        ) or document
        await publisher.publish_document(document)

        fallback_pages = await asyncio.to_thread(extract_pdf_text, pdf_path)
        title, total_pages, records = build_chunks(
            document_id=document_id,
            filename=document["filename"],
            structured=structured,
            parsed_dir=parsed_dir,
            captions=captions,
            fallback_pages=fallback_pages,
        )
        if not records:
            raise RuntimeError("解析完成但没有提取到可索引的文本或图像")
        replace_chunks(document_id, records)

        elapsed = time.monotonic() - started
        document = update_document(
            document_id,
            status=STATUS_COMPLETED,
            progress=100,
            stage="解析完成",
            title=title,
            page_count=max(total_pages, 1),
            parse_seconds=round(elapsed, 2),
            completed_at=now_iso(),
            error=None,
        ) or document
        await publisher.publish_document(document)
        logger.info(
            "document %s completed: %s chunks, %s images, %.1fs",
            document_id,
            len(records),
            sum(1 for record in records if record.get("image_path")),
            elapsed,
        )
        return document

    except Exception as exc:
        logger.exception("document %s failed", document_id)
        document = update_document(
            document_id,
            status=STATUS_FAILED,
            stage="解析失败",
            error=str(exc)[:1200],
        ) or document
        await publisher.publish_document(document)
        raise
