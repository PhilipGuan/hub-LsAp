"""解析后端调度。

``PARSER_BACKEND`` 三种取值：

* ``auto``   ：优先用 MinerU，不可用/失败时自动降级到内置解析器（默认）
* ``mineru`` ：强制 MinerU，不可用即报错（作业/答辩演示推荐，确保确实走了 MinerU）
* ``lite``   ：强制内置解析器（无需下载模型，克隆即跑）

失败降级只针对 ``auto``，并且会把原因写进 ``ParsedDocument.warnings``，
最终展示在文档详情里，避免「悄悄降级」。
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path

from ..config import settings
from . import lite_backend, mineru_backend
from .base import Block, ParsedDocument, ParseError
from .chunker import build_chunks

logger = logging.getLogger(__name__)

Progress = Callable[[float, str], None]

__all__ = [
    "Block",
    "build_chunks",
    "ParsedDocument",
    "ParseError",
    "parse_pdf",
    "backend_status",
    "resolve_backend",
]


def resolve_backend() -> tuple[str, str]:
    """返回 (实际后端, 说明)。"""
    configured = settings.parser_backend
    if configured == "lite":
        return "lite", "已按 PARSER_BACKEND=lite 使用内置解析器"
    available, message = mineru_backend.probe()
    if configured == "mineru":
        return ("mineru" if available else "mineru-unavailable"), message
    if available:
        return "mineru", message
    return "lite", f"MinerU 不可用（{message}），自动降级到内置解析器"


def _render_pages(pdf_path: Path, doc_dir: Path, report: Progress) -> None:
    """统一生成页面图：两种后端都受益（引用预览、页面级视觉问答）。"""
    pages_dir = doc_dir / "pages"
    if pages_dir.is_dir() and any(pages_dir.glob("page-*.png")):
        return
    report(0.05, "渲染页面")
    started = time.time()
    try:
        from .pages import render_pages

        rendered = render_pages(pdf_path, pages_dir, dpi=settings.page_image_dpi)
        logger.info("渲染 %s 页，用时 %.1fs", len(rendered), time.time() - started)
    except Exception as exc:  # noqa: BLE001 - 页面图只是增强，失败不阻塞
        logger.warning("页面渲染失败（忽略）: %s", exc)


def parse_pdf(
    pdf_path: Path,
    doc_dir: Path,
    progress: Progress | None = None,
    *,
    backend: str | None = None,
) -> ParsedDocument:
    """解析 PDF 到 ``doc_dir``。"""
    report = progress or (lambda _value, _stage: None)
    doc_dir.mkdir(parents=True, exist_ok=True)
    (doc_dir / "images").mkdir(parents=True, exist_ok=True)
    (doc_dir / "pages").mkdir(parents=True, exist_ok=True)

    chosen = backend or settings.parser_backend
    parsed: ParsedDocument | None = None
    mineru_error: str | None = None

    if chosen in {"auto", "mineru"}:
        available, message = mineru_backend.probe()
        if not available and chosen == "mineru":
            raise ParseError(f"已指定 PARSER_BACKEND=mineru，但 MinerU 不可用：{message}")
        if available:
            report(0.1, "MinerU 解析中")
            try:
                parsed = mineru_backend.parse(pdf_path, doc_dir, report)
            except ParseError as exc:
                mineru_error = str(exc)
                logger.warning("MinerU 解析失败: %s", exc)
                if chosen == "mineru":
                    raise
            except Exception as exc:  # noqa: BLE001 - 后端异常统一转 ParseError
                mineru_error = f"{type(exc).__name__}: {exc}"
                logger.exception("MinerU 解析异常")
                if chosen == "mineru":
                    raise ParseError(f"MinerU 解析异常：{mineru_error}") from exc

    if parsed is None:
        report(0.12, "内置解析器解析中")
        parsed = lite_backend.parse(pdf_path, doc_dir, report)
        if mineru_error:
            parsed.warnings.append(f"MinerU 解析失败，已降级到内置解析器：{mineru_error[:300]}")

    _render_pages(pdf_path, doc_dir, report)

    # 统一保存 Markdown 与 content_list，方便复核解析质量
    try:
        (doc_dir / "markdown.md").write_text(parsed.markdown or "", encoding="utf-8")
        import json

        (doc_dir / "content_list.json").write_text(
            json.dumps(parsed.to_content_list(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:  # pragma: no cover - 磁盘异常
        logger.warning("保存解析中间结果失败: %s", exc)

    report(0.92, "解析完成")
    return parsed


def backend_status() -> dict[str, object]:
    backend, message = resolve_backend()
    available, detail = mineru_backend.probe()
    return {
        "configured": settings.parser_backend,
        "active": backend,
        "mineru_available": available,
        "detail": message or detail,
    }
